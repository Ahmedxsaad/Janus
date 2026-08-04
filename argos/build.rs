//! Generates the Tauri context (config, embedded frontend assets) at build time.

fn main() {
    tauri_build::build()
}
