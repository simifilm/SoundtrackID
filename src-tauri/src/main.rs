// Prevents an additional console window on Windows in release mode.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::TcpListener;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use tauri::Manager;

fn find_free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .expect("no free port available")
        .local_addr()
        .unwrap()
        .port()
}

/// In production: return path to the bundled fiwi-server binary inside the .app.
/// In dev: return None so we fall back to spawning via Python.
fn find_bundled_server(app: &tauri::App) -> Option<PathBuf> {
    let bin = app
        .path()
        .resource_dir()
        .ok()?
        .join("fiwi-server")
        .join("fiwi-server");
    if bin.exists() { Some(bin) } else { None }
}

fn find_python() -> String {
    if let Ok(p) = std::env::var("FIWI_PYTHON") {
        return p;
    }
    #[cfg(target_os = "windows")]
    let venv = std::path::Path::new(".venv/Scripts/python.exe");
    #[cfg(not(target_os = "windows"))]
    let venv = std::path::Path::new(".venv/bin/python");
    if venv.exists() {
        return venv.to_str().unwrap().to_owned();
    }
    if cfg!(target_os = "windows") { "python".to_owned() } else { "python3".to_owned() }
}

fn wait_for_server(port: u16) -> bool {
    use std::net::TcpStream;
    let addr = format!("127.0.0.1:{}", port);
    let deadline = Instant::now() + Duration::from_secs(60);
    while Instant::now() < deadline {
        if TcpStream::connect(addr.as_str()).is_ok() {
            thread::sleep(Duration::from_millis(300));
            return true;
        }
        thread::sleep(Duration::from_millis(200));
    }
    false
}

fn main() {
    let port = find_free_port();

    // We need to know the resource dir before spawning, but Tauri's path resolver
    // isn't available until after Builder::build(). Work around this by checking
    // the standard macOS bundle layout directly.
    let bundled_bin: Option<PathBuf> = {
        if let Ok(exe) = std::env::current_exe() {
            #[cfg(target_os = "macos")]
            // Inside a .app: exe is at Contents/MacOS/, resources at Contents/Resources/
            let candidate = exe
                .parent()
                .and_then(|p| p.parent())
                .map(|p| p.join("Resources").join("fiwi-server").join("fiwi-server"))
                .filter(|p| p.exists());

            #[cfg(target_os = "windows")]
            // On Windows (NSIS/MSI), Tauri places resources in the install dir alongside the exe
            let candidate = exe
                .parent()
                .map(|p| p.join("fiwi-server").join("fiwi-server.exe"))
                .filter(|p| p.exists());

            #[cfg(not(any(target_os = "macos", target_os = "windows")))]
            let candidate: Option<PathBuf> = None;

            candidate
        } else {
            None
        }
    };

    let mut cmd = if let Some(ref bin) = bundled_bin {
        let mut c = Command::new(bin);
        c.args(["--port", &port.to_string(), "--host", "127.0.0.1"]);
        c
    } else {
        // Dev fallback: spawn via Python interpreter
        let python = find_python();
        let mut c = Command::new(&python);
        c.args(["-m", "fiwi_filmmusik", "--port", &port.to_string(), "--host", "127.0.0.1"]);
        c
    };

    cmd.stdout(Stdio::inherit()).stderr(Stdio::inherit());

    let proc = match cmd.spawn() {
        Ok(p) => p,
        Err(e) => {
            eprintln!("Failed to start server: {}", e);
            eprintln!("Set FIWI_PYTHON=/path/to/venv/bin/python and try again.");
            std::process::exit(1);
        }
    };

    let child: Arc<Mutex<Option<Child>>> = Arc::new(Mutex::new(Some(proc)));
    let child_for_event = child.clone();

    if !wait_for_server(port) {
        eprintln!("Server did not become ready within 60 seconds.");
        if let Some(mut p) = child.lock().unwrap().take() {
            let _ = p.kill();
        }
        std::process::exit(1);
    }

    tauri::Builder::default()
        .setup(move |app| {
            let _ = find_bundled_server(app); // satisfies the Manager import
            tauri::WebviewWindowBuilder::new(
                app,
                "main",
                tauri::WebviewUrl::External(
                    format!("http://127.0.0.1:{}/", port)
                        .parse()
                        .expect("invalid server URL"),
                ),
            )
            .title("FIWI Filmmusik")
            .inner_size(1280.0, 860.0)
            .min_inner_size(900.0, 600.0)
            .build()?;
            Ok(())
        })
        .on_window_event(move |_window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                if let Some(mut proc) = child_for_event.lock().unwrap().take() {
                    let _ = proc.kill();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error running tauri application");
}
