//! One shell kept open for an agent, as DeepSeek Harness's persistent `pwsh`
//! tool does on Windows: starting `powershell.exe` for every command costs a
//! second each and, with no console to share, used to open a window each time.
//!
//! The shell starts on the first command, hidden, in the agent's work dir, and
//! runs a small read loop. Each command goes in as one line — an id and the
//! command's UTF-8 bytes in base64, so no quoting or newline can break the
//! framing — and is dot-sourced into the loop's scope, so variables, functions
//! and imported modules carry over to the next call. Its stdout and stderr come
//! back merged, up to an end marker that carries the id and the exit code.
//!
//! Every command starts in the work dir again (the caller anchors it): a `cd`
//! must not move where the next command runs, or a path check that reads one
//! command at a time could be walked out of the workspace.
//!
//! Reset, never repair: a timeout, a cancelled call, a shell that exits
//! (`exit`) or a broken pipe kills the shell's whole process tree, and the next
//! command starts a fresh one. A program that waits on stdin blocks until the
//! timeout, as it does in DeepSeek's tool.

use std::path::PathBuf;
use std::process::Stdio;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

use base64::Engine;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin, ChildStdout, Command};
use tokio::sync::Mutex;

const MARKER: &str = "\u{1f}ROOT-DONE";
/// Output kept per command; the tool result truncates further.
const MAX_OUTPUT_BYTES: usize = 4 * 1024 * 1024;

/// The loop Windows PowerShell 5.1 and PowerShell 7 both run. `$global:LASTEXITCODE`
/// starts at `$null` so a native program's own code (0 included) can be told
/// from "no native program ran", where errors decide.
const POWERSHELL_LOOP: &str = r#"$ErrorActionPreference = 'Continue'; $ProgressPreference = 'SilentlyContinue'
$root_utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $root_utf8; $OutputEncoding = $root_utf8
$root_mark = [string][char]31 + 'ROOT-DONE'
while ($null -ne ($root_line = [Console]::In.ReadLine())) {
  $root_parts = $root_line.Split(' ', 2)
  $root_errors = $Error.Count; $global:LASTEXITCODE = $null; $root_failed = $false
  try {
    . ([ScriptBlock]::Create([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($root_parts[1])))) 2>&1 |
      Out-String -Stream -Width 4096 | ForEach-Object { [Console]::Out.WriteLine($_) }
  } catch {
    [Console]::Out.WriteLine(($_ | Out-String).TrimEnd()); $root_failed = $true
  }
  if ($root_failed) { $root_code = 1 }
  elseif ($null -ne $global:LASTEXITCODE) { $root_code = $global:LASTEXITCODE }
  elseif ($Error.Count -gt $root_errors) { $root_code = 1 }
  else { $root_code = 0 }
  [Console]::Out.WriteLine($root_mark + ' ' + $root_parts[0] + ' ' + $root_code)
  [Console]::Out.Flush()
}"#;

/// The same loop in bash: the POSIX twin, which the tests drive on every system.
const BASH_LOOP: &str = r#"while IFS=' ' read -r root_id root_b64; do
  root_cmd=$(printf '%s' "$root_b64" | base64 -d)
  eval "$root_cmd" </dev/null 2>&1
  printf '\037ROOT-DONE %s %s\n' "$root_id" "$?"
done"#;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Dialect {
    PowerShell,
    Bash,
}

#[derive(Debug, Default)]
pub struct Outcome {
    pub output: String,
    /// The command's exit code; `None` when it never finished.
    pub exit_code: Option<i32>,
    pub timed_out: bool,
    /// The shell itself ended (`exit`, a crash): the next command starts a fresh one.
    pub shell_exited: bool,
}

struct Live {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
}

pub struct PersistentShell {
    program: String,
    dialect: Dialect,
    work_dir: PathBuf,
    live: Mutex<Option<Live>>,
    ids: AtomicU64,
}

/// Kills the shell unless the command it is running settles: a call that is
/// dropped mid-command (the turn was cancelled) must not leave it running.
struct InFlight<'a> {
    live: &'a mut Option<Live>,
    settled: bool,
}

impl Drop for InFlight<'_> {
    fn drop(&mut self) {
        if !self.settled {
            if let Some(live) = self.live.take() {
                kill_tree(live.child);
            }
        }
    }
}

impl PersistentShell {
    pub fn new(program: impl Into<String>, dialect: Dialect, work_dir: impl Into<PathBuf>) -> Self {
        Self {
            program: program.into(),
            dialect,
            work_dir: work_dir.into(),
            live: Mutex::new(None),
            ids: AtomicU64::new(1),
        }
    }

    pub fn dialect(&self) -> Dialect {
        self.dialect
    }

    fn spawn(&self) -> anyhow::Result<Live> {
        let mut command = Command::new(&self.program);
        match self.dialect {
            Dialect::PowerShell => {
                command.args([
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    POWERSHELL_LOOP,
                ]);
            }
            Dialect::Bash => {
                command.args(["--noprofile", "--norc", "-c", BASH_LOOP]);
            }
        }
        command
            .current_dir(&self.work_dir)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .env("NO_COLOR", "1")
            .env("TERM", "dumb")
            .env("PAGER", "cat")
            .env("GIT_PAGER", "cat")
            .kill_on_drop(true);
        kaos::no_window(&mut command);
        #[cfg(unix)]
        command.process_group(0);
        let mut child = command.spawn()?;
        let stdin = child.stdin.take().ok_or_else(|| anyhow::anyhow!("the shell has no stdin"))?;
        let stdout = child.stdout.take().ok_or_else(|| anyhow::anyhow!("the shell has no stdout"))?;
        Ok(Live { child, stdin, stdout: BufReader::new(stdout) })
    }

    /// Run one command in the shell, starting it first if needed. Errors only
    /// when the shell cannot be started; everything else is in the outcome.
    pub async fn run(&self, command: &str, timeout: Duration) -> anyhow::Result<Outcome> {
        let mut guard = self.live.lock().await; // one command at a time per agent
        if guard.is_none() {
            *guard = Some(self.spawn()?);
        }
        let id = self.ids.fetch_add(1, Ordering::Relaxed);
        let encoded = base64::engine::general_purpose::STANDARD.encode(command.as_bytes());
        let mut flight = InFlight { live: &mut *guard, settled: false };
        let live = flight.live.as_mut().expect("started above");
        let mut outcome = Outcome::default();

        let sent = async {
            live.stdin.write_all(format!("{id} {encoded}\n").as_bytes()).await?;
            live.stdin.flush().await
        }
        .await;
        if sent.is_err() {
            // The shell died since the last command; the caller sees it as exited.
            outcome.shell_exited = true;
            return Ok(outcome);
        }

        let marker = format!("{MARKER} {id} ");
        let output = &mut outcome.output;
        let read = tokio::time::timeout(timeout, async {
            let mut line = Vec::new();
            loop {
                line.clear();
                if live.stdout.read_until(b'\n', &mut line).await? == 0 {
                    return Ok::<Option<i32>, std::io::Error>(None);
                }
                let text = String::from_utf8_lossy(&line);
                let bare = text.trim_end_matches(['\r', '\n']);
                if let Some(code) = bare.strip_prefix(&marker) {
                    return Ok(Some(code.trim().parse().unwrap_or(1)));
                }
                if output.len() < MAX_OUTPUT_BYTES {
                    output.push_str(bare);
                    output.push('\n');
                }
            }
        })
        .await;

        match read {
            Ok(Ok(Some(code))) => {
                outcome.exit_code = Some(code);
                flight.settled = true;
            }
            Ok(Ok(None)) | Ok(Err(_)) => outcome.shell_exited = true, // dropped below: reset
            Err(_) => outcome.timed_out = true,
        }
        Ok(outcome)
    }
}

/// Stop the shell and everything it started.
fn kill_tree(mut child: Child) {
    if let Some(pid) = child.id() {
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            let _ = std::process::Command::new("taskkill")
                .args(["/T", "/F", "/PID", &pid.to_string()])
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .creation_flags(0x0800_0000)
                .status();
        }
        #[cfg(unix)]
        {
            // The shell leads its own process group (`process_group(0)`).
            let _ = std::process::Command::new("kill")
                .args(["-KILL", &format!("-{pid}")])
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
        }
    }
    let _ = child.start_kill();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[cfg(unix)]
    fn bash(dir: &std::path::Path) -> PersistentShell {
        PersistentShell::new("bash", Dialect::Bash, dir)
    }

    #[cfg(unix)] // Windows' bash.exe is the WSL launcher; the runtime uses PowerShell there
    #[tokio::test]
    async fn state_carries_over_and_exit_codes_come_back() {
        let dir = tempfile::tempdir().unwrap();
        let shell = bash(dir.path());
        let first = shell.run("root_x=41; echo \"pid:$$\"", Duration::from_secs(10)).await.unwrap();
        assert_eq!(first.exit_code, Some(0));
        let second = shell.run("echo \"$((root_x + 1)) pid:$$\"; echo oops >&2; false", Duration::from_secs(10)).await.unwrap();
        assert_eq!(second.exit_code, Some(1));
        assert!(second.output.contains("42"), "{second:?}");
        assert!(second.output.contains("oops"), "stderr is merged: {second:?}");
        let pid = |o: &Outcome| o.output.split("pid:").nth(1).unwrap().split_whitespace().next().unwrap().to_string();
        assert_eq!(pid(&first), pid(&second), "one shell for both commands");
        let multi = shell.run("for i in 1 2; do\n  echo \"line $i 'quoted' \\\"too\\\"\"\ndone", Duration::from_secs(10)).await.unwrap();
        assert_eq!(multi.output, "line 1 'quoted' \"too\"\nline 2 'quoted' \"too\"\n");
    }

    #[cfg(unix)] // Windows' bash.exe is the WSL launcher; the runtime uses PowerShell there
    #[tokio::test]
    async fn a_timeout_or_an_exit_starts_the_next_command_in_a_fresh_shell() {
        let dir = tempfile::tempdir().unwrap();
        let shell = bash(dir.path());
        shell.run("root_x=1", Duration::from_secs(10)).await.unwrap();
        let slow = shell.run("echo started; sleep 30", Duration::from_millis(700)).await.unwrap();
        assert!(slow.timed_out && slow.exit_code.is_none() && slow.output.contains("started"), "{slow:?}");
        let after = shell.run("echo \"x=${root_x:-unset}\"", Duration::from_secs(10)).await.unwrap();
        assert_eq!(after.output.trim(), "x=unset");
        let gone = shell.run("exit 3", Duration::from_secs(10)).await.unwrap();
        assert!(gone.shell_exited, "{gone:?}");
        let fresh = shell.run("echo again", Duration::from_secs(10)).await.unwrap();
        assert_eq!((fresh.output.trim(), fresh.exit_code), ("again", Some(0)));
    }

    #[cfg(unix)] // Windows' bash.exe is the WSL launcher; the runtime uses PowerShell there
    #[tokio::test]
    async fn a_cancelled_command_does_not_keep_running() {
        let dir = tempfile::tempdir().unwrap();
        let beat = dir.path().join("beat");
        let shell = bash(dir.path());
        let command = format!("while true; do echo x >> '{}'; sleep 0.05; done", beat.display());
        let cancelled = tokio::time::timeout(Duration::from_millis(500), shell.run(&command, Duration::from_secs(60))).await;
        assert!(cancelled.is_err(), "the call was dropped mid-command");
        tokio::time::sleep(Duration::from_millis(300)).await;
        let size = std::fs::metadata(&beat).unwrap().len();
        tokio::time::sleep(Duration::from_millis(400)).await;
        assert_eq!(std::fs::metadata(&beat).unwrap().len(), size, "the loop outlived its call");
        let next = shell.run("echo ok", Duration::from_secs(10)).await.unwrap();
        assert_eq!(next.output.trim(), "ok");
    }

    #[cfg(windows)]
    #[tokio::test]
    async fn powershell_keeps_its_state_between_commands() {
        let dir = tempfile::tempdir().unwrap();
        let shell = PersistentShell::new("powershell.exe", Dialect::PowerShell, dir.path());
        let first = shell.run("$root_x = 41; function Get-RootY { 'why' }; Write-Output \"pid:$PID\"", Duration::from_secs(60)).await.unwrap();
        assert_eq!(first.exit_code, Some(0), "{first:?}");
        let second = shell
            .run("Write-Output ($root_x + 1); Get-RootY; Write-Output \"pid:$PID\"; cmd /c exit 7", Duration::from_secs(60))
            .await
            .unwrap();
        assert_eq!(second.exit_code, Some(7), "{second:?}");
        assert!(second.output.contains("42") && second.output.contains("why"), "{second:?}");
        let pid = |o: &Outcome| o.output.split("pid:").nth(1).unwrap().split_whitespace().next().unwrap().to_string();
        assert_eq!(pid(&first), pid(&second), "one PowerShell for both commands");
        let failed = shell.run("Get-Item C:\\definitely\\not\\here", Duration::from_secs(60)).await.unwrap();
        assert_eq!(failed.exit_code, Some(1), "{failed:?}");
        let ok_native = shell.run("cmd /c \"echo to-stderr 1>&2\"", Duration::from_secs(60)).await.unwrap();
        assert_eq!(ok_native.exit_code, Some(0), "a native program's stderr is not a failure: {ok_native:?}");
        let slow = shell.run("Write-Output started; Start-Sleep -Seconds 30", Duration::from_secs(5)).await.unwrap();
        assert!(slow.timed_out, "{slow:?}");
        let fresh = shell.run("Write-Output \"x=$root_x\"", Duration::from_secs(60)).await.unwrap();
        assert_eq!(fresh.output.trim(), "x=", "{fresh:?}");
    }
}
