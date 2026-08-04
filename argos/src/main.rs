// A release build must not open a console window on Windows. The parent still
// hands this process inherited stdin and stdout pipe handles, which is the
// whole transport (docs/plan/08 section 6).
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! Argos: the window half of the watchdog.
//!
//! This binary owns pixels and nothing else. It reads newline-delimited JSON
//! events on stdin, forwards each one to the webview, and writes the commands a
//! user clicked back on stdout. It never talks to DataHub, holds no
//! credentials, and binds no port: the producer that spawned it does all of
//! that (docs/plan/08 section 6).
//!
//! Two rules keep the transport honest, and both are load-bearing:
//!
//! * stdout carries commands and nothing else. Everything this process has to
//!   say about itself goes to stderr, because a stray line on stdout would be
//!   read by the parent as a command.
//! * a line that is not a JSON object is dropped rather than forwarded. GTK and
//!   WebKit are not always polite about which stream they log to, and one
//!   warning printed into our channel must not reach the state machine.

use std::io::{BufRead, Write};

use tauri::{AppHandle, Emitter, Manager};

/// The event name the frontend listens on.
const EVENT: &str = "argos://event";

/// Return the line the webview should receive, or None when it must be dropped.
///
/// Blank lines and anything that is not a JSON *object* are dropped. Being
/// strict about the object shape (rather than accepting any valid JSON) is what
/// makes a stray `1234` or a bare string from an unrelated library harmless.
fn accept(line: &str) -> Option<String> {
    let trimmed = line.trim();
    if trimmed.is_empty() {
        return None;
    }
    match serde_json::from_str::<serde_json::Value>(trimmed) {
        Ok(serde_json::Value::Object(_)) => Some(trimmed.to_owned()),
        _ => None,
    }
}

/// Forward a command from the window to the producer, one JSON line on stdout.
///
/// Flushed on every write: line buffering is the contract, and a command the
/// user clicked must not sit in a buffer until the next one arrives.
#[tauri::command]
fn send_command(line: String) {
    let stdout = std::io::stdout();
    let mut handle = stdout.lock();
    if writeln!(handle, "{line}").is_ok() {
        let _ = handle.flush();
    }
}

/// Show or hide the full-screen overlay the blast-radius walk animates in.
///
/// The pet window is 176px and repositioning it once per animation frame is
/// jank that some window managers rate-limit. The walk gets its own maximised,
/// click-through window instead, which exists only while a path is playing.
#[tauri::command]
fn set_walk_overlay(app: AppHandle, visible: bool) {
    let Some(window) = app.get_webview_window("walk") else {
        return;
    };
    if visible {
        // Click-through before it is shown: a full-screen window that swallows
        // every click, even for the second it takes to set this, is the kind of
        // bug that gets a desktop toy uninstalled.
        let _ = window.set_ignore_cursor_events(true);
        let _ = window.maximize();
        let _ = window.show();
    } else {
        let _ = window.hide();
    }
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![send_command, set_walk_overlay])
        .setup(|app| {
            let handle = app.handle().clone();
            // A dedicated thread, because reading stdin blocks and the UI
            // thread must stay free to draw. Losing stdin (the producer exited)
            // ends the loop and leaves the window up: closing it here would
            // race the producer's own shutdown, and the user closing the window
            // is a separate decision from the producer stopping.
            std::thread::spawn(move || {
                let stdin = std::io::stdin();
                for line in stdin.lock().lines() {
                    let Ok(line) = line else { break };
                    if let Some(event) = accept(&line) {
                        if handle.emit(EVENT, event).is_err() {
                            break;
                        }
                    }
                }
            });
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("argos: failed to start the window");
}

#[cfg(test)]
mod tests {
    use super::accept;

    #[test]
    fn a_json_object_is_forwarded_without_its_whitespace() {
        assert_eq!(
            accept("  {\"v\":1,\"state\":\"patrolling\"}\n").as_deref(),
            Some("{\"v\":1,\"state\":\"patrolling\"}")
        );
    }

    #[test]
    fn blank_and_non_object_lines_are_dropped() {
        assert_eq!(accept(""), None);
        assert_eq!(accept("   "), None);
        // What a GTK warning or a partial write looks like on the wrong stream.
        assert_eq!(accept("Gtk-WARNING **: cannot open display"), None);
        assert_eq!(accept("{\"v\":1,\"state\":"), None);
        assert_eq!(accept("[1,2,3]"), None);
        assert_eq!(accept("42"), None);
    }
}
