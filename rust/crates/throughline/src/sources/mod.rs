//! Sources that do not fit the adapter contract. An adapter parses one file
//! without a database client, and its runner hashes that file. A git
//! repository is a folder, and its runner reads the store to skip a
//! repository whose branches did not move, so it runs on its own.

pub mod git;

use std::path::PathBuf;

/// The roots in the colon-separated variable `var`, `~` expanded against
/// `$HOME`, read the way the Markdown adapter reads its roots. Unset or
/// empty: no roots.
pub fn roots_from_env(var: &str) -> Vec<PathBuf> {
    let raw = std::env::var(var).unwrap_or_default();
    let home = std::env::var_os("HOME")
        .filter(|h| !h.is_empty())
        .map(PathBuf::from)
        .or_else(dirs::home_dir);
    crate::adapters::markdown::parse_roots(&raw, home.as_deref())
}
