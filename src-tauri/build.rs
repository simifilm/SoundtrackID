use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

fn main() {
    ensure_icons_fetched();
    tauri_build::build()
}

fn ensure_icons_fetched() {
    println!("cargo:rerun-if-changed=icons");

    let icons_dir = Path::new("icons");
    let pointers: Vec<PathBuf> = match fs::read_dir(icons_dir) {
        Ok(entries) => entries
            .filter_map(|e| e.ok())
            .map(|e| e.path())
            .filter(|p| p.extension().and_then(|s| s.to_str()) == Some("png"))
            .filter(|p| is_lfs_pointer(p))
            .collect(),
        Err(_) => return,
    };

    if pointers.is_empty() {
        return;
    }

    println!(
        "cargo:warning=Found {} unfetched Git LFS pointer(s) in src-tauri/icons; running `git lfs pull`",
        pointers.len()
    );

    let status = Command::new("git")
        .args(["lfs", "pull", "--include=src-tauri/icons/*"])
        .current_dir("..")
        .status();

    match status {
        Ok(s) if s.success() => {
            for p in &pointers {
                if is_lfs_pointer(p) {
                    panic!(
                        "`git lfs pull` succeeded but {} is still a pointer. \
                         Run `git lfs install && git lfs pull` manually.",
                        p.display()
                    );
                }
            }
        }
        Ok(s) => panic!(
            "`git lfs pull` exited with {s}. \
             Install git-lfs (https://git-lfs.com), then run: git lfs install && git lfs pull"
        ),
        Err(e) => panic!(
            "Could not run `git lfs pull`: {e}. \
             Install git-lfs (https://git-lfs.com), then run: git lfs install && git lfs pull"
        ),
    }
}

fn is_lfs_pointer(path: &Path) -> bool {
    fs::read(path)
        .map(|b| b.starts_with(b"version https://git-lfs.github.com/spec/v1"))
        .unwrap_or(false)
}
