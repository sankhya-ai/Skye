use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::Serialize;
use thiserror::Error;

pub const MAX_TASKS_ENV: &str = "CHOTU_MAX_SUBAGENT_TASKS";
pub const MAX_PARALLEL_TASKS_ENV: &str = "CHOTU_MAX_PARALLEL_SUBAGENTS";
pub const MAX_TURN_TOKENS_ENV: &str = "CHOTU_MAX_TURN_TOKENS";
pub const MAX_TURN_WALL_SECONDS_ENV: &str = "CHOTU_MAX_TURN_WALL_SECONDS";
pub const MAX_TURN_COST_USD_ENV: &str = "CHOTU_MAX_TURN_COST_USD";

pub const TASK_BUDGET_EXHAUSTED: &str = "Chotu council task budget exhausted";
pub const PARALLELISM_BUDGET_EXHAUSTED: &str = "Chotu council parallelism budget exhausted";
pub const TOKEN_BUDGET_EXHAUSTED: &str = "Chotu turn token budget exhausted";
pub const WALL_CLOCK_BUDGET_EXHAUSTED: &str = "Chotu turn wall-clock budget exhausted";
pub const COST_BUDGET_EXHAUSTED: &str = "Chotu turn cost budget exhausted";

const USD_NANOS: f64 = 1_000_000_000.0;

#[derive(Clone, Copy, Debug, Error, Eq, PartialEq)]
#[error("{reason}")]
pub struct CouncilBudgetExceeded {
    pub reason: &'static str,
}

impl CouncilBudgetExceeded {
    pub fn new(reason: &'static str) -> Self {
        Self { reason }
    }
}

#[derive(Debug)]
pub struct CouncilBudget {
    max_tasks: usize,
    max_parallel_tasks: usize,
    tasks_started: AtomicUsize,
    tasks_running: AtomicUsize,
    max_tokens: Option<u64>,
    tokens_used: AtomicU64,
    max_cost_nanos: Option<u64>,
    cost_used_nanos: AtomicU64,
    max_wall_clock: Option<Duration>,
    started_at: Mutex<Instant>,
}

impl CouncilBudget {
    pub fn from_environment() -> Arc<Self> {
        Arc::new(Self::with_optional_limits(
            env_limit(MAX_TASKS_ENV).unwrap_or(8),
            env_limit(MAX_PARALLEL_TASKS_ENV).unwrap_or(4),
            env_u64(MAX_TURN_TOKENS_ENV),
            env_u64(MAX_TURN_WALL_SECONDS_ENV).map(Duration::from_secs),
            env_cost_nanos(MAX_TURN_COST_USD_ENV),
        ))
    }

    pub fn new(max_tasks: usize, max_parallel_tasks: usize) -> Self {
        Self::with_optional_limits(max_tasks, max_parallel_tasks, None, None, None)
    }

    pub fn with_limits(
        max_tasks: usize,
        max_parallel_tasks: usize,
        max_tokens: u64,
        max_wall_clock: Duration,
    ) -> Self {
        Self::with_optional_limits(
            max_tasks,
            max_parallel_tasks,
            Some(max_tokens.max(1)),
            Some(max_wall_clock.max(Duration::from_millis(1))),
            None,
        )
    }

    pub fn with_cost_limit(
        max_tasks: usize,
        max_parallel_tasks: usize,
        max_tokens: u64,
        max_wall_clock: Duration,
        max_cost_usd: f64,
    ) -> Self {
        Self::with_optional_limits(
            max_tasks,
            max_parallel_tasks,
            Some(max_tokens.max(1)),
            Some(max_wall_clock.max(Duration::from_millis(1))),
            Some(usd_to_nanos(max_cost_usd).max(1)),
        )
    }

    fn with_optional_limits(
        max_tasks: usize,
        max_parallel_tasks: usize,
        max_tokens: Option<u64>,
        max_wall_clock: Option<Duration>,
        max_cost_nanos: Option<u64>,
    ) -> Self {
        Self {
            max_tasks: max_tasks.max(1),
            max_parallel_tasks: max_parallel_tasks.max(1),
            tasks_started: AtomicUsize::new(0),
            tasks_running: AtomicUsize::new(0),
            max_tokens,
            tokens_used: AtomicU64::new(0),
            max_cost_nanos,
            cost_used_nanos: AtomicU64::new(0),
            max_wall_clock,
            started_at: Mutex::new(Instant::now()),
        }
    }

    pub fn reset(&self) {
        self.tasks_started.store(0, Ordering::SeqCst);
        self.tasks_running.store(0, Ordering::SeqCst);
        self.tokens_used.store(0, Ordering::SeqCst);
        self.cost_used_nanos.store(0, Ordering::SeqCst);
        *self
            .started_at
            .lock()
            .expect("council budget clock poisoned") = Instant::now();
    }

    pub fn acquire(self: &Arc<Self>) -> Result<CouncilPermit, &'static str> {
        self.check_token_budget()?;
        self.check_cost_budget()?;
        self.check_wall_clock()?;
        let started = self.tasks_started.fetch_add(1, Ordering::SeqCst);
        if started >= self.max_tasks {
            self.tasks_started.fetch_sub(1, Ordering::SeqCst);
            return Err(TASK_BUDGET_EXHAUSTED);
        }
        let running = self.tasks_running.fetch_add(1, Ordering::SeqCst);
        if running >= self.max_parallel_tasks {
            self.tasks_running.fetch_sub(1, Ordering::SeqCst);
            self.tasks_started.fetch_sub(1, Ordering::SeqCst);
            return Err(PARALLELISM_BUDGET_EXHAUSTED);
        }
        Ok(CouncilPermit {
            budget: Arc::clone(self),
        })
    }

    pub fn charge_tokens(&self, tokens: u64) -> Result<(), &'static str> {
        let Some(max_tokens) = self.max_tokens else {
            return Ok(());
        };
        if self.record_tokens(tokens) <= max_tokens {
            Ok(())
        } else {
            Err(TOKEN_BUDGET_EXHAUSTED)
        }
    }

    pub fn record_tokens(&self, tokens: u64) -> u64 {
        self.tokens_used
            .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |current| {
                Some(current.saturating_add(tokens))
            })
            .unwrap_or_else(|current| current)
            .saturating_add(tokens)
    }

    pub fn check_token_budget(&self) -> Result<(), &'static str> {
        match self.max_tokens {
            Some(max_tokens) if self.tokens_used.load(Ordering::SeqCst) >= max_tokens => {
                Err(TOKEN_BUDGET_EXHAUSTED)
            }
            _ => Ok(()),
        }
    }

    pub fn charge_cost_usd(&self, cost_usd: f64) -> Result<(), &'static str> {
        let next = self.record_cost_usd(cost_usd);
        match self.max_cost_nanos {
            Some(maximum) if next > maximum => Err(COST_BUDGET_EXHAUSTED),
            _ => Ok(()),
        }
    }

    pub fn record_cost_usd(&self, cost_usd: f64) -> u64 {
        let charge = usd_to_nanos(cost_usd);
        self.cost_used_nanos
            .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |current| {
                Some(current.saturating_add(charge))
            })
            .unwrap_or_else(|current| current)
            .saturating_add(charge)
    }

    pub fn check_cost_budget(&self) -> Result<(), &'static str> {
        match self.max_cost_nanos {
            Some(maximum) if self.cost_used_nanos.load(Ordering::SeqCst) >= maximum => {
                Err(COST_BUDGET_EXHAUSTED)
            }
            _ => Ok(()),
        }
    }

    pub fn remaining_wall_time(&self) -> Result<Option<Duration>, &'static str> {
        let Some(max_wall_clock) = self.max_wall_clock else {
            return Ok(None);
        };
        let elapsed = self
            .started_at
            .lock()
            .expect("council budget clock poisoned")
            .elapsed();
        max_wall_clock
            .checked_sub(elapsed)
            .filter(|remaining| !remaining.is_zero())
            .map(Some)
            .ok_or(WALL_CLOCK_BUDGET_EXHAUSTED)
    }

    pub fn check_wall_clock(&self) -> Result<(), &'static str> {
        self.remaining_wall_time().map(|_| ())
    }

    pub fn snapshot(&self) -> CouncilBudgetSnapshot {
        CouncilBudgetSnapshot {
            max_tasks: self.max_tasks,
            max_parallel_tasks: self.max_parallel_tasks,
            tasks_started: self.tasks_started.load(Ordering::SeqCst),
            tasks_running: self.tasks_running.load(Ordering::SeqCst),
            max_tokens: self.max_tokens,
            tokens_used: self.tokens_used.load(Ordering::SeqCst),
            max_cost_usd: self.max_cost_nanos.map(nanos_to_usd),
            cost_used_usd: nanos_to_usd(self.cost_used_nanos.load(Ordering::SeqCst)),
            max_wall_clock_millis: self.max_wall_clock.map(|value| value.as_millis() as u64),
            elapsed_millis: self
                .started_at
                .lock()
                .expect("council budget clock poisoned")
                .elapsed()
                .as_millis() as u64,
        }
    }
}

#[derive(Debug)]
pub struct CouncilPermit {
    budget: Arc<CouncilBudget>,
}

impl Drop for CouncilPermit {
    fn drop(&mut self) {
        self.budget.tasks_running.fetch_sub(1, Ordering::SeqCst);
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Serialize)]
pub struct CouncilBudgetSnapshot {
    pub max_tasks: usize,
    pub max_parallel_tasks: usize,
    pub tasks_started: usize,
    pub tasks_running: usize,
    pub max_tokens: Option<u64>,
    pub tokens_used: u64,
    pub max_cost_usd: Option<f64>,
    pub cost_used_usd: f64,
    pub max_wall_clock_millis: Option<u64>,
    pub elapsed_millis: u64,
}

fn env_limit(name: &str) -> Option<usize> {
    std::env::var(name)
        .ok()
        .and_then(|value| value.parse::<usize>().ok())
        .filter(|value| *value > 0)
}

fn env_u64(name: &str) -> Option<u64> {
    std::env::var(name)
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .filter(|value| *value > 0)
}

fn env_cost_nanos(name: &str) -> Option<u64> {
    std::env::var(name)
        .ok()
        .and_then(|value| value.parse::<f64>().ok())
        .filter(|value| value.is_finite() && *value > 0.0)
        .map(usd_to_nanos)
        .filter(|value| *value > 0)
}

fn usd_to_nanos(value: f64) -> u64 {
    if !value.is_finite() || value <= 0.0 {
        return 0;
    }
    (value * USD_NANOS).round().min(u64::MAX as f64) as u64
}

fn nanos_to_usd(value: u64) -> f64 {
    value as f64 / USD_NANOS
}
