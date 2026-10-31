use std::sync::Arc;
use std::time::Duration;

use kimi_agent::council_budget::{
    COST_BUDGET_EXHAUSTED, CouncilBudget, TOKEN_BUDGET_EXHAUSTED, WALL_CLOCK_BUDGET_EXHAUSTED,
};

#[test]
fn council_budget_enforces_total_and_parallel_limits() {
    let budget = Arc::new(CouncilBudget::new(2, 1));

    let first = budget.acquire().expect("first task permit");
    assert_eq!(
        budget
            .acquire()
            .expect_err("parallel task must be rejected"),
        "Chotu council parallelism budget exhausted"
    );
    drop(first);

    let second = budget.acquire().expect("second task permit");
    drop(second);
    assert_eq!(
        budget
            .acquire()
            .expect_err("total task budget must be exhausted"),
        "Chotu council task budget exhausted"
    );
}

#[test]
fn council_budget_enforces_and_resets_provider_cost_receipts() {
    let budget = CouncilBudget::with_cost_limit(2, 1, 100, Duration::from_secs(1), 0.05);

    budget.charge_cost_usd(0.02).expect("first receipt");
    budget.charge_cost_usd(0.03).expect("exact cost budget");
    assert_eq!(budget.check_cost_budget(), Err(COST_BUDGET_EXHAUSTED));
    assert_eq!(budget.charge_cost_usd(0.001), Err(COST_BUDGET_EXHAUSTED));
    assert!((budget.snapshot().cost_used_usd - 0.051).abs() < 1e-9);

    budget.reset();
    budget.charge_cost_usd(0.01).expect("reset cost budget");
    assert!((budget.snapshot().cost_used_usd - 0.01).abs() < 1e-9);
}

#[test]
fn council_budget_reset_starts_a_new_turn() {
    let budget = Arc::new(CouncilBudget::new(1, 1));
    drop(budget.acquire().expect("first turn permit"));
    budget.reset();

    let permit = budget.acquire().expect("new turn permit");
    let snapshot = budget.snapshot();
    assert_eq!(snapshot.tasks_started, 1);
    assert_eq!(snapshot.tasks_running, 1);
    drop(permit);
}

#[test]
fn council_budget_atomically_enforces_and_resets_turn_tokens() {
    let budget = CouncilBudget::with_limits(2, 1, 100, Duration::from_secs(1));

    budget.charge_tokens(60).expect("first usage charge");
    budget.charge_tokens(40).expect("exact budget charge");
    assert_eq!(budget.charge_tokens(1), Err(TOKEN_BUDGET_EXHAUSTED));
    assert_eq!(budget.snapshot().tokens_used, 101);
    assert_eq!(budget.check_token_budget(), Err(TOKEN_BUDGET_EXHAUSTED));

    budget.reset();
    budget.charge_tokens(100).expect("reset token budget");
    assert_eq!(budget.snapshot().tokens_used, 100);
}

#[test]
fn council_budget_rejects_work_after_monotonic_deadline() {
    let budget = Arc::new(CouncilBudget::with_limits(
        2,
        1,
        100,
        Duration::from_millis(5),
    ));
    std::thread::sleep(Duration::from_millis(10));

    assert_eq!(budget.check_wall_clock(), Err(WALL_CLOCK_BUDGET_EXHAUSTED));
    assert_eq!(
        budget.acquire().expect_err("deadline must block new tasks"),
        WALL_CLOCK_BUDGET_EXHAUSTED
    );
}
