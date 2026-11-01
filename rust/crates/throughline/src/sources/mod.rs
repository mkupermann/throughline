//! Sources that do not fit the adapter contract. An adapter parses one file
//! without a database client, and its runner hashes that file. A git
//! repository is a folder, and its runner reads the store to skip a
//! repository whose branches did not move, so it runs on its own. A web
//! page is a URL, and its runner reads the store to fetch a known page
//! again only on request.

pub mod git;
pub mod web;

use std::path::PathBuf;

/// The roots in the colon-separated variable `var`, `~` expanded against
/// `$HOME`, read the way the Markdown adapter reads its roots. Unset or
/// empty: no roots.
pub fn roots_from_env(var: &str) -> Vec<PathBuf> {
    read_roots_env(var).0
}

/// [`roots_from_env`], plus the entries it dropped because they are not
/// absolute after expansion.
pub fn read_roots_env(var: &str) -> (Vec<PathBuf>, Vec<String>) {
    use crate::adapters::markdown::parse_roots;
    let raw = std::env::var(var).unwrap_or_default();
    let home = std::env::var_os("HOME")
        .filter(|h| !h.is_empty())
        .map(PathBuf::from)
        .or_else(dirs::home_dir);
    let dropped = raw
        .split(':')
        .map(str::trim)
        .filter(|e| !e.is_empty() && parse_roots(e, home.as_deref()).is_empty())
        .map(String::from)
        .collect();
    (parse_roots(&raw, home.as_deref()), dropped)
}
