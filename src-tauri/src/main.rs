// Desktop shell: starts the Python engine (sidecar) on a random loopback port, waits for it to
// announce `T2MD_PORT=<n>`, then opens the webview on http://127.0.0.1:<n>/ (the engine also serves the UI).
// The engine is stopped when the app exits. Conversions are resumable, so a hard kill never loses work.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};

struct Engine(Mutex<Option<Child>>);

fn engine_command(static_dir: &PathBuf) -> Command {
    // 1) packaged sidecar (PyInstaller build, see scripts/build_engine.sh) next to the executable
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let name = if cfg!(windows) { "textbook2md-engine.exe" } else { "textbook2md-engine" };
            let cand = dir.join(name);
            if cand.exists() {
                let mut c = Command::new(cand);
                c.args(["serve", "--port", "0", "--static"]).arg(static_dir);
                return c;
            }
        }
    }
    // 2) development: `python -m textbook2md` from the repository (T2MD_PYTHON overrides the interpreter)
    let py = std::env::var("T2MD_PYTHON").unwrap_or_else(|_| if cfg!(windows) { "python".into() } else { "python3".into() });
    let mut c = Command::new(py);
    c.args(["-m", "textbook2md", "serve", "--port", "0", "--static"]).arg(static_dir);
    if let Ok(cwd) = std::env::current_dir() {
        let engine_dir = cwd.join("../engine");
        if engine_dir.exists() {
            c.env("PYTHONPATH", engine_dir);
        }
    }
    c
}

fn start_engine(static_dir: PathBuf) -> Result<(Child, u16), String> {
    let mut child = engine_command(&static_dir)
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| format!("cannot start the textbook2md engine: {e}. Install Python 3.10+ and `pip install -e .`, or build the sidecar with scripts/build_engine.sh"))?;
    let stdout = child.stdout.take().ok_or("engine has no stdout")?;
    for line in BufReader::new(stdout).lines().map_while(Result::ok) {
        if let Some(p) = line.strip_prefix("T2MD_PORT=") {
            let port: u16 = p.trim().parse().map_err(|_| "bad port from engine".to_string())?;
            return Ok((child, port));
        }
    }
    Err("engine exited before announcing its port".into())
}

fn main() {
    let app = tauri::Builder::default()
        .setup(|app| {
            let res = app.path().resource_dir().ok().map(|d| d.join("dist")).filter(|d| d.exists());
            let static_dir = res.unwrap_or_else(|| PathBuf::from("../app/dist"));
            let (child, port) = start_engine(static_dir).map_err(|e| Box::<dyn std::error::Error>::from(e))?;
            app.manage(Engine(Mutex::new(Some(child))));
            let url = format!("http://127.0.0.1:{port}/");
            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url.parse().unwrap()))
                .title("textbook2md")
                .inner_size(1440.0, 920.0)
                .min_inner_size(960.0, 640.0)
                .build()?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the textbook2md desktop shell");
    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            if let Some(engine) = handle.try_state::<Engine>() {
                if let Some(mut child) = engine.0.lock().unwrap().take() {
                    let _ = child.kill();
                }
            }
        }
    });
}
