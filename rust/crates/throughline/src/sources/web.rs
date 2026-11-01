//! Web pages as a source.
//!
//! `ingest --source web` reads the URL file named by `THROUGHLINE_WEB_URLS`
//! (an absolute path, `~` expanded against `$HOME`; one URL per line, a line
//! starting with `#` is a comment). Without the variable or the file the
//! source does nothing. `ingest --source all` leaves web pages out: a page is
//! fetched only when the web source is asked for by name.
//!
//! A URL is fetched once. A known URL (one in the ingestion log, a declined
//! page included) is fetched again only with `--refetch`. A refetch whose
//! text did not change writes nothing; a changed page replaces the stored one.
//!
//! What goes out: one GET per page, one per `robots.txt` and host, each with
//! `User-Agent: Throughline/<version>` and an `Accept` header. No body, no
//! cookies (reqwest is built without a cookie store), no credentials: the
//! userinfo and the fragment leave every URL before anything else happens to
//! it, redirect targets included. TLS is checked against the platform's trust
//! store, and nothing here turns that off.
//!
//! Protection:
//! - only `http` and `https`;
//! - SSRF: the host is resolved, and every address must be public. Refused:
//!   loopback, private (10/8, 172.16/12, 192.168/16), link-local, CGNAT
//!   (100.64/10), unique-local (fc00::/7), multicast, 0.0.0.0/8, 240/4 with
//!   the broadcast address, the unspecified address, and IPv4 inside IPv6
//!   (mapped `::ffff:0:0/96` and compatible `::/96`). The checked addresses
//!   are pinned for the connection, so a second lookup cannot swap them. Every
//!   redirect hop is checked the same way, at most 5 redirects. Loopback is
//!   allowed only by a [`Policy`] that exists in tests;
//! - with a proxy from `HTTPS_PROXY` or `HTTP_PROXY` (reqwest reads them), the
//!   check still runs before the request, but the proxy resolves the host
//!   itself. A name that resolves to another address for the proxy (DNS
//!   rebinding) is not caught, and the proxy decides what it may reach;
//! - `robots.txt` per RFC 9309, for every host a page or redirect comes from:
//!   the groups naming `Throughline`, else the `*` groups; the longest
//!   matching `Allow` or `Disallow` wins, `Allow` on a tie. A 4xx (404
//!   included) allows everything; a 5xx, a timeout or a network error
//!   disallows everything. Its fetch counts toward the budget;
//! - only `text/html` and `text/plain`; at most 2 MiB, counted while the body
//!   streams in; 10 s for everything one URL needs (lookups, robots,
//!   redirects, body).
//!
//! One URL is one conversation:
//! - `session_id` is a stable UUID of `web/<url>` (the URL as listed, cleaned,
//!   not the redirect target); `project_path` is `web/<host>`, so the project
//!   is the host; `summary` is the `<title>`, else the URL; `source_tool` is
//!   `web`; metadata: `url`, `final_url`, `etag`, `fetched_at`, `status`,
//!   `content_type`;
//! - HTML becomes text with `html2text`: a heading comes out as a marked line
//!   and becomes a `#` heading, a text line that would read as a heading or a
//!   code fence is indented by four spaces, and `<noscript>` is dropped.
//!   `text/plain` is taken as it is. The Markdown splitter then cuts at H1 to
//!   H3, and a section over 2,000 characters goes on in further messages, each
//!   starting with the heading line again;
//! - role `document`, no uuid, so a refetch deletes and rewrites. Message n is
//!   stamped fetch time plus n µs, and never at or before the newest stored
//!   message, so the extraction sees each version as new;
//! - a page without text (one that needs JavaScript) is recorded as declined
//!   in the ingestion log, and nothing else is stored.
//!
//! Known limits: bodies are read as UTF-8 (other charsets come out lossy);
//! there is no conditional GET; deeply nested HTML converts slowly (100,000
//! nested elements took about 11 s in a release build).

use crate::adapters::markdown::{section_parts, split_sections, Section};
use crate::adapters::stable_uuid;
use crate::ignore::IgnoreRules;
use crate::types::{IngestSummary, NormalisedConversation, NormalisedMessage, Role};
use crate::writer as w;
use anyhow::{anyhow, bail, Context, Result};
use chrono::{DateTime, Utc};
use html2text::render::TextDecorator;
use postgres::GenericClient;
use reqwest::blocking::Response;
use reqwest::header::{ACCEPT, CONTENT_TYPE, ETAG, LOCATION};
use reqwest::Url;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::io::Read;
use std::net::{IpAddr, Ipv4Addr, SocketAddr, ToSocketAddrs};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};
use uuid::Uuid;

/// Source name, as `ingest --source` takes it and `source_tool` stores it.
pub const NAME: &str = "web";
/// Path of the URL file: one URL per line, `#` comment lines.
pub const URLS_ENV: &str = "THROUGHLINE_WEB_URLS";
/// Largest page body read.
pub const MAX_BODY_BYTES: u64 = 2 * 1024 * 1024;
/// Time for everything one URL needs.
pub const BUDGET: Duration = Duration::from_secs(10);
/// Redirects followed per fetch.
pub const MAX_REDIRECTS: usize = 5;
/// The only header that names the client.
pub const USER_AGENT: &str = concat!("Throughline/", env!("CARGO_PKG_VERSION"));

/// The product token `robots.txt` groups are matched against, lower case.
const ROBOTS_TOKEN: &str = "throughline";
/// RFC 9309 asks for at least 500 KiB to be parsed; the rest is ignored.
const MAX_ROBOTS_BYTES: u64 = 500 * 1024;
const PAGE_ACCEPT: &str = "text/html, text/plain;q=0.9";
/// Marks a heading line in html2text's output, once per level.
const HEADING_MARK: char = '\u{E000}';
/// Wide enough that a paragraph stays one line.
const TEXT_WIDTH: usize = 10_000;
const MAX_TITLE_CHARS: usize = 300;

const NS: Uuid = Uuid::from_u128(0x7c1d_9a52_3e64_4b0f_a8d3_25e9_b14f_c607);

/// Which addresses may be fetched from. Production refuses loopback; only
/// tests can build a policy that allows it, and no variable switches it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Policy {
    allow_loopback: bool,
}

impl Policy {
    pub const PRODUCTION: Policy = Policy {
        allow_loopback: false,
    };

    /// Loopback allowed, for a test server on 127.0.0.1.
    #[cfg(test)]
    pub(crate) const fn allowing_loopback() -> Policy {
        Policy {
            allow_loopback: true,
        }
    }
}

/// The URL file and the address policy.
#[derive(Debug, Clone)]
pub struct WebSource {
    url_file: Option<PathBuf>,
    policy: Policy,
}

impl WebSource {
    /// The file in [`URLS_ENV`]. Unset or empty: no file, a no-op.
    pub fn from_env() -> Self {
        Self::new(super::roots_from_env(URLS_ENV).into_iter().next())
    }

    pub fn new(url_file: Option<PathBuf>) -> Self {
        Self {
            url_file,
            policy: Policy::PRODUCTION,
        }
    }

    #[cfg(test)]
    pub(crate) fn with_policy(mut self, policy: Policy) -> Self {
        self.policy = policy;
        self
    }

    pub fn url_file(&self) -> Option<&Path> {
        self.url_file.as_deref()
    }

    /// The URL lines with their line numbers. No file configured: none.
    fn entries(&self) -> std::io::Result<Vec<(usize, String)>> {
        let Some(path) = &self.url_file else {
            return Ok(Vec::new());
        };
        let text = std::fs::read_to_string(path)?;
        Ok(text
            .lines()
            .enumerate()
            .map(|(n, line)| (n + 1, line.trim()))
            .filter(|(_, line)| !line.is_empty() && !line.starts_with('#'))
            .map(|(n, line)| (n, line.to_string()))
            .collect())
    }

    /// The configured URL lines; 0 without a readable file.
    pub fn url_count(&self) -> usize {
        self.entries().map_or(0, |e| e.len())
    }
}

/// `web: <n> urls configured`, and a note when the file cannot be read.
pub fn doctor_line(source: &WebSource) -> String {
    let n = source.url_count();
    let unreadable = source.url_file.is_some() && source.entries().is_err();
    format!(
        "web: {n} {} configured{}",
        if n == 1 { "url" } else { "urls" },
        if unreadable {
            ", URL file not readable"
        } else {
            ""
        }
    )
}

/// `raw` as a URL to fetch: `http` or `https` with a host, the userinfo and
/// the fragment removed. An error names no part of `raw`, so it cannot
/// repeat a password.
pub fn clean_url(raw: &str) -> std::result::Result<Url, String> {
    let url = Url::parse(raw.trim()).map_err(|e| format!("not a URL ({e})"))?;
    clean(url)
}

fn clean(mut url: Url) -> std::result::Result<Url, String> {
    // First, so no message, key or request ever carries them.
    let _ = url.set_username("");
    let _ = url.set_password(None);
    url.set_fragment(None);
    if !matches!(url.scheme(), "http" | "https") {
        return Err(format!("only http and https, not {}:", url.scheme()));
    }
    if url.host_str().is_none_or(str::is_empty) {
        return Err("the URL has no host".into());
    }
    Ok(url)
}

/// Why `ip` may not be fetched from; `None` when it may.
fn refused(ip: IpAddr, policy: Policy) -> Option<&'static str> {
    let v6 = match ip {
        IpAddr::V4(v4) => return refused_v4(v4, policy),
        IpAddr::V6(v6) => v6,
    };
    let first = v6.segments()[0];
    if v6.to_ipv4_mapped().is_some() {
        Some("IPv4-mapped IPv6")
    } else if v6.is_loopback() {
        (!policy.allow_loopback).then_some("loopback")
    } else if v6.is_unspecified() {
        Some("unspecified")
    } else if v6.segments()[..6] == [0; 6] {
        Some("IPv4-compatible IPv6")
    } else if v6.is_multicast() {
        Some("multicast")
    } else if first & 0xfe00 == 0xfc00 {
        Some("unique-local")
    } else if first & 0xffc0 == 0xfe80 {
        Some("link-local")
    } else {
        None
    }
}

fn refused_v4(ip: Ipv4Addr, policy: Policy) -> Option<&'static str> {
    let [a, b, ..] = ip.octets();
    if ip.is_loopback() {
        (!policy.allow_loopback).then_some("loopback")
    } else if a == 0 {
        Some("0.0.0.0/8")
    } else if ip.is_private() {
        Some("private")
    } else if ip.is_link_local() {
        Some("link-local")
    } else if a == 100 && b & 0xc0 == 64 {
        Some("CGNAT")
    } else if ip.is_multicast() {
        Some("multicast")
    } else if a >= 240 {
        Some("reserved")
    } else {
        None
    }
}

/// Time left before `deadline`, an error once it is spent.
fn remaining(deadline: Instant) -> Result<Duration> {
    deadline
        .checked_duration_since(Instant::now())
        .filter(|d| !d.is_zero())
        .ok_or_else(|| anyhow!("the {} s budget ran out", BUDGET.as_secs()))
}

/// A host's checked addresses; `domain` is set when they came from a lookup
/// and must be pinned for the connection.
struct Target {
    domain: Option<String>,
    addrs: Vec<SocketAddr>,
}

/// Resolve `url`'s host within the budget and refuse it unless every address
/// is allowed by `policy`.
fn checked(url: &Url, policy: Policy, deadline: Instant) -> Result<Target> {
    let host = url.host_str().context("the URL has no host")?;
    let port = url.port_or_known_default().context("the URL has no port")?;
    let literal = host
        .strip_prefix('[')
        .and_then(|h| h.strip_suffix(']'))
        .unwrap_or(host);
    let target = if let Ok(ip) = literal.parse::<IpAddr>() {
        Target {
            domain: None,
            addrs: vec![SocketAddr::new(ip, port)],
        }
    } else {
        // getaddrinfo cannot be cancelled: wait on a thread for the budget.
        let (send, recv) = std::sync::mpsc::channel();
        let name = host.to_string();
        std::thread::spawn(move || {
            let _ = send.send(
                (name.as_str(), port)
                    .to_socket_addrs()
                    .map(Iterator::collect::<Vec<_>>),
            );
        });
        let addrs = recv
            .recv_timeout(remaining(deadline)?)
            .map_err(|_| anyhow!("looking up {host} took longer than the budget"))?
            .with_context(|| format!("look up {host}"))?;
        Target {
            domain: Some(host.to_string()),
            addrs,
        }
    };
    if target.addrs.is_empty() {
        bail!("{host} has no address");
    }
    for addr in &target.addrs {
        if let Some(why) = refused(addr.ip(), policy) {
            bail!("refused: {host} has the {why} address {}", addr.ip());
        }
    }
    Ok(target)
}

/// One GET to `target`'s checked addresses, no redirect followed.
fn send(url: &Url, target: &Target, deadline: Instant, accept: &str) -> Result<Response> {
    let mut builder = reqwest::blocking::Client::builder()
        .user_agent(USER_AGENT)
        .redirect(reqwest::redirect::Policy::none())
        .timeout(remaining(deadline)?);
    if let Some(domain) = &target.domain {
        builder = builder.resolve_to_addrs(domain, &target.addrs);
    }
    let client = builder.build().context("build the HTTP client")?;
    client
        .get(url.clone())
        .header(ACCEPT, accept)
        .send()
        .with_context(|| format!("GET {url}"))
}

/// The cleaned target of a redirect, `None` for any other response.
fn redirect_target(current: &Url, resp: &Response) -> Result<Option<Url>> {
    if !matches!(resp.status().as_u16(), 301 | 302 | 303 | 307 | 308) {
        return Ok(None);
    }
    let location = resp
        .headers()
        .get(LOCATION)
        .and_then(|v| v.to_str().ok())
        .context("a redirect without a usable Location")?;
    let next = current
        .join(location)
        .context("a redirect to an unreadable Location")?;
    clean(next)
        .map(Some)
        .map_err(|e| anyhow!("refused: a redirect to a URL that is not allowed: {e}"))
}

/// The body up to `cap` bytes, and whether there was more. Reading stops
/// at `cap + 1` bytes or at the deadline.
fn read_body(mut body: impl Read, cap: u64, deadline: Instant) -> Result<(Vec<u8>, bool)> {
    let mut out = Vec::new();
    let mut chunk = vec![0u8; 64 * 1024];
    loop {
        remaining(deadline)?;
        let n = body.read(&mut chunk).context("read the body")?;
        if n == 0 {
            return Ok((out, false));
        }
        out.extend_from_slice(&chunk[..n]);
        if out.len() as u64 > cap {
            out.truncate(cap as usize);
            return Ok((out, true));
        }
    }
}

/// A fetched page.
struct Page {
    /// The URL that answered, after redirects.
    url: Url,
    status: u16,
    /// The media type, lower case, without parameters.
    content_type: String,
    etag: Option<String>,
    body: Vec<u8>,
    fetched_at: DateTime<Utc>,
}

fn read_page(url: Url, resp: Response, deadline: Instant) -> Result<Page> {
    let status = resp.status();
    if !status.is_success() {
        bail!("HTTP {status}");
    }
    let header = |name| {
        resp.headers()
            .get(name)
            .and_then(|v: &reqwest::header::HeaderValue| v.to_str().ok())
            .map(str::to_string)
    };
    let content_type = header(CONTENT_TYPE)
        .unwrap_or_default()
        .split(';')
        .next()
        .unwrap_or_default()
        .trim()
        .to_ascii_lowercase();
    if content_type != "text/html" && content_type != "text/plain" {
        bail!("refused: content type {content_type:?}, only text/html and text/plain are read");
    }
    let too_large = || anyhow!("refused: the body is larger than 2 MiB");
    if resp.content_length().is_some_and(|n| n > MAX_BODY_BYTES) {
        return Err(too_large());
    }
    let etag = header(ETAG);
    let (body, over) = read_body(resp, MAX_BODY_BYTES, deadline)?;
    if over {
        return Err(too_large());
    }
    Ok(Page {
        url,
        status: status.as_u16(),
        content_type,
        etag,
        body,
        fetched_at: Utc::now(),
    })
}

/// One `Allow` or `Disallow` line of the group that applies to us.
#[derive(Debug, Clone, PartialEq, Eq)]
struct Rule {
    allow: bool,
    pattern: String,
}

/// The rules in `robots.txt` text that apply to the product token
/// `Throughline` (RFC 9309): the groups naming it, else the `*` groups,
/// merged. A user-agent line after a rule starts a new group.
fn robots_rules(text: &str) -> Vec<Rule> {
    let (mut ours, mut star) = (Vec::new(), Vec::new());
    let (mut agents, mut in_rules, mut named_us) = (Vec::<String>::new(), false, false);
    for line in text.lines() {
        let line = line.split('#').next().unwrap_or_default().trim();
        let Some((key, value)) = line.split_once(':') else {
            continue;
        };
        let (key, value) = (key.trim().to_ascii_lowercase(), value.trim());
        if key == "user-agent" {
            if in_rules {
                agents.clear();
                in_rules = false;
            }
            let token = if value.starts_with('*') {
                "*".to_string()
            } else {
                value
                    .chars()
                    .take_while(|c| c.is_ascii_alphabetic() || *c == '_' || *c == '-')
                    .collect::<String>()
                    .to_ascii_lowercase()
            };
            named_us |= token == ROBOTS_TOKEN;
            agents.push(token);
        } else if key == "allow" || key == "disallow" {
            in_rules = true;
            if value.is_empty() {
                continue;
            }
            let rule = Rule {
                allow: key == "allow",
                pattern: percent_encode_non_ascii(value),
            };
            if agents.iter().any(|a| a == ROBOTS_TOKEN) {
                ours.push(rule.clone());
            }
            if agents.iter().any(|a| a == "*") {
                star.push(rule);
            }
        }
    }
    if named_us {
        ours
    } else {
        star
    }
}

/// URL paths are percent-encoded; patterns are compared in the same form.
fn percent_encode_non_ascii(pattern: &str) -> String {
    pattern
        .bytes()
        .map(|b| {
            if b.is_ascii() {
                char::from(b).to_string()
            } else {
                format!("%{b:02X}")
            }
        })
        .collect()
}

/// Whether `path` (path and query) may be fetched: the longest matching
/// pattern decides, `Allow` on a tie; no match allows.
fn robots_allows(rules: &[Rule], path: &str) -> bool {
    path == "/robots.txt"
        || rules
            .iter()
            .filter(|r| pattern_matches(&r.pattern, path))
            .max_by_key(|r| (r.pattern.len(), r.allow))
            .is_none_or(|r| r.allow)
}

/// A robots pattern: a prefix, `*` for any run of characters, `$` at the
/// end for the end of the path.
fn pattern_matches(pattern: &str, path: &str) -> bool {
    let (pattern, anchored) = match pattern.strip_suffix('$') {
        Some(p) => (p, true),
        None => (pattern, false),
    };
    let mut parts = pattern.split('*');
    let Some(mut rest) = path.strip_prefix(parts.next().unwrap_or_default()) else {
        return false;
    };
    let parts: Vec<&str> = parts.collect();
    let Some((last, middle)) = parts.split_last() else {
        return !anchored || rest.is_empty();
    };
    for part in middle {
        match rest.find(part) {
            Some(i) => rest = &rest[i + part.len()..],
            None => return false,
        }
    }
    if anchored {
        rest.ends_with(last)
    } else {
        rest.contains(last)
    }
}

enum RobotsFile {
    Rules(Vec<Rule>),
    /// 5xx, timeout or network error: nothing may be fetched.
    Unreachable(String),
}

/// Fetches pages and remembers each host's `robots.txt` for the run.
struct Fetcher {
    policy: Policy,
    budget: Duration,
    robots: HashMap<String, RobotsFile>,
}

impl Fetcher {
    fn new(policy: Policy) -> Self {
        Self {
            policy,
            budget: BUDGET,
            robots: HashMap::new(),
        }
    }

    /// `url` within the budget: each hop checked, robots asked, at most
    /// [`MAX_REDIRECTS`] redirects.
    fn fetch(&mut self, url: &Url) -> Result<Page> {
        let deadline = Instant::now() + self.budget;
        let mut current = url.clone();
        for _ in 0..=MAX_REDIRECTS {
            let target = checked(&current, self.policy, deadline)?;
            self.robots_check(&current, deadline)?;
            let resp = send(&current, &target, deadline, PAGE_ACCEPT)?;
            match redirect_target(&current, &resp)? {
                Some(next) => current = next,
                None => return read_page(current, resp, deadline),
            }
        }
        bail!("refused: more than {MAX_REDIRECTS} redirects")
    }

    fn robots_check(&mut self, url: &Url, deadline: Instant) -> Result<()> {
        let origin = url.origin().ascii_serialization();
        if !self.robots.contains_key(&origin) {
            let file = self.robots_file(url, deadline);
            self.robots.insert(origin.clone(), file);
        }
        let mut path = url.path().to_string();
        if let Some(query) = url.query() {
            path.push('?');
            path.push_str(query);
        }
        match &self.robots[&origin] {
            RobotsFile::Rules(rules) if robots_allows(rules, &path) => Ok(()),
            RobotsFile::Rules(_) => bail!("refused: robots.txt disallows {path}"),
            RobotsFile::Unreachable(why) => {
                bail!("refused: robots.txt unreachable ({why}), so nothing there is fetched")
            }
        }
    }

    /// RFC 9309: a 2xx gives rules, a 4xx none (all allowed), anything
    /// else makes the host unreachable (all disallowed).
    fn robots_file(&self, url: &Url, deadline: Instant) -> RobotsFile {
        let unreachable = |e: anyhow::Error| RobotsFile::Unreachable(format!("{e:#}"));
        let Ok(mut current) = url.join("/robots.txt") else {
            return RobotsFile::Unreachable("no robots.txt URL".into());
        };
        for _ in 0..=MAX_REDIRECTS {
            let resp = match checked(&current, self.policy, deadline)
                .and_then(|t| send(&current, &t, deadline, "text/plain"))
            {
                Ok(resp) => resp,
                Err(e) => return unreachable(e),
            };
            match redirect_target(&current, &resp) {
                Ok(Some(next)) => {
                    current = next;
                    continue;
                }
                Ok(None) => {}
                Err(e) => return unreachable(e),
            }
            let status = resp.status();
            if status.is_success() {
                return match read_body(resp, MAX_ROBOTS_BYTES, deadline) {
                    Ok((body, _)) => {
                        RobotsFile::Rules(robots_rules(&String::from_utf8_lossy(&body)))
                    }
                    Err(e) => unreachable(e),
                };
            }
            if status.is_client_error() {
                return RobotsFile::Rules(Vec::new());
            }
            return RobotsFile::Unreachable(format!("HTTP {status}"));
        }
        // More than five redirects: unavailable, which RFC 9309 allows.
        RobotsFile::Rules(Vec::new())
    }
}

/// html2text's plain text without decoration. A heading starts with
/// [`HEADING_MARK`] once per level; links and images leave their text only.
#[derive(Clone, Debug)]
struct Marked;

impl TextDecorator for Marked {
    type Annotation = ();

    fn decorate_link_start(&mut self, _url: &str) -> (String, ()) {
        (String::new(), ())
    }
    fn decorate_link_end(&mut self) -> String {
        String::new()
    }
    fn decorate_em_start(&self) -> (String, ()) {
        (String::new(), ())
    }
    fn decorate_em_end(&self) -> String {
        String::new()
    }
    fn decorate_strong_start(&self) -> (String, ()) {
        (String::new(), ())
    }
    fn decorate_strong_end(&self) -> String {
        String::new()
    }
    fn decorate_strikeout_start(&self) -> (String, ()) {
        (String::new(), ())
    }
    fn decorate_strikeout_end(&self) -> String {
        String::new()
    }
    fn decorate_code_start(&self) -> (String, ()) {
        (String::new(), ())
    }
    fn decorate_code_end(&self) -> String {
        String::new()
    }
    fn decorate_preformat_first(&self) {}
    fn decorate_preformat_cont(&self) {}
    fn decorate_image(&mut self, _src: &str, _title: &str) -> (String, ()) {
        (String::new(), ())
    }
    fn header_prefix(&self, level: usize) -> String {
        HEADING_MARK.to_string().repeat(level) + " "
    }
    fn quote_prefix(&self) -> String {
        "> ".into()
    }
    fn unordered_item_prefix(&self) -> String {
        "* ".into()
    }
    fn ordered_item_prefix(&self, i: i64) -> String {
        format!("{i}. ")
    }
    fn make_subblock_decorator(&self) -> Self {
        Marked
    }
}

/// HTML as Markdown-shaped text: `#` headings, everything else text.
fn html_to_text(html: &[u8]) -> Result<String> {
    let config = html2text::config::with_decorator(Marked)
        .no_table_borders()
        .allow_width_overflow();
    let dom = config.parse_html(html).context("parse the HTML")?;
    drop_noscript(&dom);
    let tree = config
        .dom_to_render_tree(&dom)
        .context("lay out the HTML")?;
    let text = config
        .render_to_string(tree, TEXT_WIDTH)
        .context("render the HTML as text")?;
    Ok(as_markdown(&text))
}

/// `<noscript>` holds what a browser without JavaScript shows, usually a
/// request to turn it on. Without it, a page that needs JavaScript has no
/// text and is declined.
fn drop_noscript(dom: &html2text::RcDom) {
    let mut stack = vec![dom.document.clone()];
    while let Some(node) = stack.pop() {
        node.children.borrow_mut().retain(|child| {
            !matches!(&child.data, html2text::Element { name, .. } if &*name.local == "noscript")
        });
        stack.extend(node.children.borrow().iter().cloned());
    }
}

/// Marked lines become `#` headings. A text line that the Markdown
/// splitter would read as a heading or a code fence (a `# comment` in a
/// code block, a paragraph starting with `#`) is indented by four spaces,
/// which makes it text.
fn as_markdown(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for line in text.lines() {
        let marks = line.chars().take_while(|&c| c == HEADING_MARK).count();
        if marks > 0 {
            out.push_str(&"#".repeat(marks));
            out.push_str(&line[marks * HEADING_MARK.len_utf8()..].replace(HEADING_MARK, ""));
        } else {
            // A heading inside a list item or a table cell is text.
            let line = line.replace(HEADING_MARK, "");
            let bare = line.trim_start_matches(' ');
            if line.len() - bare.len() <= 3
                && (bare.starts_with('#') || bare.starts_with("```") || bare.starts_with("~~~"))
            {
                out.push_str("    ");
            }
            out.push_str(&line);
        }
        out.push('\n');
    }
    out
}

/// The text of the first `<title>`, entities decoded, whitespace folded.
fn title_of(html: &str) -> Option<String> {
    // ASCII lower-casing keeps every byte offset.
    let lower = html.to_ascii_lowercase();
    let mut from = 0;
    let open = loop {
        let at = from + lower[from..].find("<title")?;
        let next = lower[at + 6..].chars().next()?;
        if next == '>' || next.is_ascii_whitespace() {
            break at;
        }
        from = at + 6;
    };
    let start = open + lower[open..].find('>')? + 1;
    let end = start + lower[start..].find("</title")?;
    let text = html2text::config::with_decorator(Marked)
        .allow_width_overflow()
        .string_from_read(
            format!("<p>{}</p>", &html[start..end]).as_bytes(),
            TEXT_WIDTH,
        )
        .ok()?;
    let title = text.split_whitespace().collect::<Vec<_>>().join(" ");
    (!title.is_empty()).then(|| title.chars().take(MAX_TITLE_CHARS).collect())
}

/// The page's title and its sections.
fn sections_of(page: &Page) -> Result<(Option<String>, Vec<Section>)> {
    let text = String::from_utf8_lossy(&page.body);
    let text = text.strip_prefix('\u{feff}').unwrap_or(&text);
    if page.content_type == "text/plain" {
        return Ok((None, split_sections(text)));
    }
    Ok((
        title_of(text),
        split_sections(&html_to_text(text.as_bytes())?),
    ))
}

enum Stored {
    Written(usize),
    Unchanged,
    Declined,
}

/// Write `page` as the conversation of `url`, replacing an earlier version.
fn store<C: GenericClient>(client: &mut C, url: &Url, page: &Page) -> Result<Stored> {
    let (title, sections) = sections_of(page)?;
    let key = PathBuf::from(url.as_str());
    let parts: Vec<(usize, usize, &Section, String)> = sections
        .iter()
        .enumerate()
        .flat_map(|(index, section)| {
            section_parts(section)
                .into_iter()
                .enumerate()
                .map(move |(part, text)| (index, part, section, text))
        })
        .collect();
    // The text, not the body: a new nonce or timestamp in the markup is
    // not a new version.
    let mut hash = Sha256::new();
    hash.update(title.as_deref().unwrap_or_default());
    for (.., text) in &parts {
        hash.update([0]);
        hash.update(text);
    }
    let hash = format!("{:x}", hash.finalize());
    if parts.is_empty() {
        w::record_decision(client, &key, &hash)?;
        return Ok(Stored::Declined);
    }
    if matches!(w::already_ingested(client, &key, &hash)?, Some(n) if n != 0) {
        return Ok(Stored::Unchanged);
    }

    let session_id = stable_uuid(&NS, None, &format!("{NAME}/{url}"));
    let mut tx = client.transaction()?;
    let newest: Option<DateTime<Utc>> = tx
        .query_one(
            "SELECT max(m.created_at) FROM messages m \
             JOIN conversations c ON c.id = m.conversation_id WHERE c.session_id = $1",
            &[&session_id],
        )?
        .get(0);
    // Microseconds, as PostgreSQL stores them; after the stored version even
    // if the clock went back.
    let fetched = DateTime::from_timestamp_micros(page.fetched_at.timestamp_micros())
        .unwrap_or(page.fetched_at);
    let base = match newest {
        Some(t) if t >= fetched => t + chrono::Duration::microseconds(1),
        _ => fetched,
    };
    let messages: Vec<NormalisedMessage> = parts
        .iter()
        .enumerate()
        .map(|(n, (index, part, section, text))| NormalisedMessage {
            role: Role::Document.as_str().into(),
            content: text.clone(),
            created_at: Some(base + chrono::Duration::microseconds(n as i64)),
            metadata: json!({
                "url": url.as_str(),
                "heading_path": section.heading_path,
                "section_index": index,
                "part": part,
            }),
            ..Default::default()
        })
        .collect();
    let conv = NormalisedConversation {
        session_id,
        project_path: Some(format!("{NAME}/{}", url.host_str().unwrap_or_default())),
        model: None,
        entrypoint: None,
        git_branch: None,
        started_at: base,
        ended_at: messages.last().and_then(|m| m.created_at),
        token_count_in: None,
        token_count_out: None,
        summary: Some(title.unwrap_or_else(|| url.to_string())),
        metadata: json!({
            "url": url.as_str(),
            "final_url": page.url.as_str(),
            "etag": page.etag,
            "fetched_at": page.fetched_at.to_rfc3339(),
            "status": page.status,
            "content_type": page.content_type,
        }),
        source_tool: Some(NAME.into()),
        messages,
    };
    let id = w::upsert_conversation(&mut tx, &conv)?;
    let written = w::replace_messages(&mut tx, id, &conv)?;
    w::record_ingest(&mut tx, &key, &hash, written)?;
    tx.commit()?;
    Ok(Stored::Written(written))
}

/// Fetch the configured pages. A known URL is fetched again only with
/// `refetch`. Ignore rules apply to the URL (`path:`) and the host
/// (`project:`), before anything is fetched.
pub fn run<C: GenericClient>(
    client: &mut C,
    source: &WebSource,
    refetch: bool,
    verbose: bool,
    rules: &IgnoreRules,
) -> Result<IngestSummary> {
    let mut summary = IngestSummary::new(NAME);
    let entries = match source.entries() {
        Ok(entries) => entries,
        Err(e) => {
            if let Some(path) = source.url_file() {
                eprintln!("[{NAME}] {}: {e}, nothing fetched", path.display());
            }
            return Ok(summary);
        }
    };
    let mut fetcher = Fetcher::new(source.policy);
    let mut seen = HashSet::new();
    for (line, raw) in entries {
        let url = match clean_url(&raw) {
            Ok(url) => url,
            Err(e) => {
                summary.files_seen += 1;
                summary.errors += 1;
                eprintln!("[{NAME}] line {line}: {e}");
                continue;
            }
        };
        if !seen.insert(url.to_string()) {
            continue;
        }
        summary.files_seen += 1;
        let key = PathBuf::from(url.as_str());
        if rules.matches_path(&key) {
            summary.ignored += 1;
            summary.ignored_path += 1;
            continue;
        }
        if rules.matches_project(url.host_str().unwrap_or_default()) {
            summary.ignored += 1;
            summary.ignored_project += 1;
            continue;
        }
        let known = w::was_seen_before(client, &key)?;
        if known && !refetch {
            summary.skipped += 1;
            if verbose {
                println!("  [{NAME}] {url}: known, not fetched (--refetch fetches it again)");
            }
            continue;
        }
        match fetcher
            .fetch(&url)
            .and_then(|page| store(client, &url, &page))
        {
            Ok(Stored::Written(n)) => {
                summary.ingested += 1;
                summary.refreshed += usize::from(known);
                summary.messages_written += n;
                if verbose {
                    println!("  [{NAME}] {url}: {n} section message(s)");
                }
            }
            Ok(Stored::Unchanged) => {
                summary.skipped += 1;
                if verbose {
                    println!("  [{NAME}] {url}: unchanged");
                }
            }
            Ok(Stored::Declined) => {
                summary.skipped += 1;
                eprintln!(
                    "[{NAME}] {url}: no text (a page that needs JavaScript?), recorded as declined"
                );
            }
            Err(e) => {
                summary.errors += 1;
                eprintln!("[{NAME}] {url}: {e:#}");
            }
        }
    }
    if summary.ingested > 0 {
        super::git::best_effort(client, |tx| w::backfill_projects(tx).map(drop));
    }
    super::git::best_effort(client, |tx| {
        crate::ingest_run::record_ignore_audit(tx, NAME, &summary);
        Ok(())
    });
    Ok(summary)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;
    use std::io::Write;
    use std::net::{Shutdown, TcpListener, TcpStream};
    use std::sync::{Arc, Mutex};

    /// Connect to the database (`PG*` env). Skip when unreachable so offline
    /// `cargo test --lib` stays green. Mirrors the writer.rs test helper.
    fn test_db() -> Option<postgres::Client> {
        use postgres::NoTls;
        let host = std::env::var("PGHOST").unwrap_or_else(|_| "localhost".into());
        let user = std::env::var("PGUSER")
            .unwrap_or_else(|_| std::env::var("USERNAME").unwrap_or_else(|_| "postgres".into()));
        let dbname = std::env::var("PGDATABASE").unwrap_or_else(|_| "throughline".into());
        let mut cfg = postgres::Config::new();
        cfg.host(&host)
            .port(
                std::env::var("PGPORT")
                    .ok()
                    .and_then(|p| p.parse().ok())
                    .unwrap_or(5432),
            )
            .user(&user)
            .dbname(&dbname);
        if let Ok(pw) = std::env::var("PGPASSWORD") {
            cfg.password(pw);
        }
        cfg.connect(NoTls).ok()
    }

    /// One canned answer of the test server.
    #[derive(Clone)]
    struct Reply {
        status: u16,
        headers: Vec<(String, String)>,
        body: Vec<u8>,
        /// Send `Content-Length`; without it the body ends when the
        /// connection closes.
        sized: bool,
        delay: Duration,
    }

    impl Reply {
        fn status(status: u16) -> Self {
            Self {
                status,
                headers: Vec::new(),
                body: Vec::new(),
                sized: true,
                delay: Duration::ZERO,
            }
        }
        fn typed(content_type: &str, body: impl Into<Vec<u8>>) -> Self {
            let mut reply = Self::status(200).header("Content-Type", content_type);
            reply.body = body.into();
            reply
        }
        fn html(body: &str) -> Self {
            Self::typed("text/html; charset=utf-8", body)
        }
        fn redirect(status: u16, to: &str) -> Self {
            Self::status(status).header("Location", to)
        }
        fn header(mut self, name: &str, value: &str) -> Self {
            self.headers.push((name.into(), value.into()));
            self
        }
        fn streamed(mut self) -> Self {
            self.sized = false;
            self
        }
        fn slow(mut self, delay: Duration) -> Self {
            self.delay = delay;
            self
        }
    }

    type Routes = Arc<Mutex<HashMap<String, Reply>>>;

    /// A local HTTP/1.1 server: one canned reply per request target, 404
    /// otherwise, every request head kept.
    struct Server {
        port: u16,
        routes: Routes,
        heads: Arc<Mutex<Vec<String>>>,
    }

    impl Server {
        fn start(routes: Vec<(&str, Reply)>) -> Self {
            let listener = TcpListener::bind("127.0.0.1:0").expect("bind a test port");
            let port = listener.local_addr().unwrap().port();
            let routes: Routes = Arc::new(Mutex::new(
                routes
                    .into_iter()
                    .map(|(path, reply)| (path.to_string(), reply))
                    .collect(),
            ));
            let heads = Arc::new(Mutex::new(Vec::new()));
            let (r, h) = (routes.clone(), heads.clone());
            std::thread::spawn(move || {
                for stream in listener.incoming().flatten() {
                    let (r, h) = (r.clone(), h.clone());
                    std::thread::spawn(move || answer(stream, &r, &h));
                }
            });
            Self {
                port,
                routes,
                heads,
            }
        }
        fn url(&self, path: &str) -> String {
            format!("http://127.0.0.1:{}{path}", self.port)
        }
        fn set(&self, path: &str, reply: Reply) {
            self.routes.lock().unwrap().insert(path.into(), reply);
        }
        fn heads(&self) -> Vec<String> {
            self.heads.lock().unwrap().clone()
        }
        fn targets(&self) -> Vec<String> {
            self.heads()
                .iter()
                .map(|h| h.split_whitespace().nth(1).unwrap_or_default().to_string())
                .collect()
        }
        fn hits(&self, target: &str) -> usize {
            self.targets().iter().filter(|t| *t == target).count()
        }
    }

    fn answer(mut stream: TcpStream, routes: &Routes, heads: &Mutex<Vec<String>>) {
        let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
        let mut head = Vec::new();
        let mut byte = [0u8; 1];
        while !head.ends_with(b"\r\n\r\n") {
            match stream.read(&mut byte) {
                Ok(1) if head.len() < 64 * 1024 => head.push(byte[0]),
                _ => return,
            }
        }
        let head = String::from_utf8_lossy(&head).into_owned();
        let target = head
            .split_whitespace()
            .nth(1)
            .unwrap_or_default()
            .to_string();
        heads.lock().unwrap().push(head);
        let reply = routes
            .lock()
            .unwrap()
            .get(&target)
            .cloned()
            .unwrap_or_else(|| Reply::status(404));
        std::thread::sleep(reply.delay);
        let mut out = format!("HTTP/1.1 {} Test\r\nConnection: close\r\n", reply.status);
        for (name, value) in &reply.headers {
            out.push_str(&format!("{name}: {value}\r\n"));
        }
        if reply.sized {
            out.push_str(&format!("Content-Length: {}\r\n", reply.body.len()));
        }
        out.push_str("\r\n");
        if stream.write_all(out.as_bytes()).is_ok() {
            for chunk in reply.body.chunks(64 * 1024) {
                if stream.write_all(chunk).is_err() {
                    break;
                }
            }
        }
        let _ = stream.shutdown(Shutdown::Both);
    }

    /// A URL file holding `lines`, and the source that reads it.
    fn source_with(lines: &[String], policy: Policy) -> (tempfile::TempDir, WebSource) {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("urls.txt");
        std::fs::write(&path, lines.join("\n") + "\n").unwrap();
        (dir, WebSource::new(Some(path)).with_policy(policy))
    }

    fn url(raw: &str) -> Url {
        clean_url(raw).unwrap()
    }

    fn counts(s: &IngestSummary) -> (usize, usize, usize, usize, usize) {
        (
            s.files_seen,
            s.ingested,
            s.skipped,
            s.errors,
            s.messages_written,
        )
    }

    fn session(url: &str) -> Uuid {
        stable_uuid(&NS, None, &format!("web/{url}"))
    }

    struct Conv {
        id: i64,
        project_path: String,
        project_name: String,
        summary: String,
        metadata: Value,
        source_tool: String,
    }

    fn conversation<C: GenericClient>(client: &mut C, url: &str) -> Option<Conv> {
        let rows = client
            .query(
                "SELECT id, project_path, project_name, summary, metadata, source_tool \
                 FROM conversations WHERE session_id = $1",
                &[&session(url)],
            )
            .unwrap();
        assert!(rows.len() <= 1, "one conversation per URL");
        rows.first().map(|r| Conv {
            id: r.get(0),
            project_path: r.get(1),
            project_name: r.get(2),
            summary: r.get(3),
            metadata: r.get(4),
            source_tool: r.get(5),
        })
    }

    struct Msg {
        id: i64,
        role: String,
        content: String,
        uuid: Option<Uuid>,
        at: DateTime<Utc>,
        metadata: Value,
    }

    fn messages<C: GenericClient>(client: &mut C, conversation_id: i64) -> Vec<Msg> {
        client
            .query(
                "SELECT id, role::text, content, uuid, created_at, metadata FROM messages \
                 WHERE conversation_id = $1 ORDER BY created_at, id",
                &[&conversation_id],
            )
            .unwrap()
            .iter()
            .map(|r| Msg {
                id: r.get(0),
                role: r.get(1),
                content: r.get(2),
                uuid: r.get(3),
                at: r.get(4),
                metadata: r.get(5),
            })
            .collect()
    }

    /// The record counts the ingestion log holds for `key`.
    fn logged<C: GenericClient>(client: &mut C, key: &str) -> Vec<i32> {
        let mut counts: Vec<i32> = client
            .query(
                "SELECT record_count FROM ingestion_log WHERE file_path = $1",
                &[&key],
            )
            .unwrap()
            .iter()
            .map(|r| r.get(0))
            .collect();
        counts.sort_unstable();
        counts
    }

    /// The guide page: text before the first heading, H1 to H4, a code
    /// block with a `#` line, a section over 2,000 characters, and markup
    /// that must not reach the text.
    fn guide_page() -> String {
        let long = "Low water stays calm along the north pier. ".repeat(60);
        format!(
            r##"<!doctype html><html><head><title>Harbour Guide &amp; Tides</title>
<script>document.title = "# not a heading";</script><style>h1 {{ color: navy }}</style></head>
<body><nav><a href="/">Home</a></nav>
<p>Printed for the harbour office.</p>
<h1>Harbour Guide</h1><p>The pier opens at <em>dawn</em>.</p>
<h2>Spring tides</h2><p>Gauge reads twice.</p>
<pre><code>interval = 10
# gauge settings</code></pre>
<h3>Neap tides</h3><p>{long}</p>
<h4>Buoys</h4><p>Red to port.</p>
<noscript>Turn on JavaScript for the tide chart.</noscript>
</body></html>"##
        )
    }

    #[test]
    fn web_fetches_a_page_into_sections() {
        let test = "web_fetches_a_page_into_sections";
        let server = Server::start(vec![
            (
                "/guide",
                Reply::html(&guide_page()).header("ETag", "\"tide-1\""),
            ),
            (
                "/app",
                Reply::html(
                    "<html><head><title>Chart</title></head><body><div id=\"root\"></div>\
                     <noscript>Turn on JavaScript to see the chart.</noscript>\
                     <script>start()</script></body></html>",
                ),
            ),
            (
                "/notes.txt",
                Reply::typed(
                    "text/plain; charset=utf-8",
                    "# Moorings\nRing 4 is loose.\n\n# Fuel\nDiesel at the north pier.\n",
                ),
            ),
            (
                "/tides.json",
                Reply::typed("application/json", "{\"high\": \"06:10\"}"),
            ),
        ]);
        let urls = ["/guide", "/app", "/notes.txt", "/tides.json"].map(|p| server.url(p));
        let (_dir, source) = source_with(&urls, Policy::allowing_loopback());
        let Some(mut client) = test_db() else {
            eprintln!("skipping {test}: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let before = Utc::now() - chrono::Duration::seconds(1);
        let s = run(&mut tx, &source, false, true, &IgnoreRules::default()).unwrap();
        // guide (5 messages) and notes (2) stored, the JavaScript page
        // declined, the JSON refused.
        assert_eq!(counts(&s), (4, 2, 1, 1, 7));

        let guide = conversation(&mut tx, &urls[0]).expect("the guide is stored");
        assert_eq!(guide.project_path, "web/127.0.0.1");
        assert_eq!(guide.project_name, "127.0.0.1");
        assert_eq!(guide.summary, "Harbour Guide & Tides");
        assert_eq!(guide.source_tool, NAME);
        assert_eq!(guide.metadata["url"], urls[0].as_str());
        assert_eq!(guide.metadata["etag"], "\"tide-1\"");
        assert_eq!(guide.metadata["status"], 200);
        assert_eq!(guide.metadata["content_type"], "text/html");
        let fetched: DateTime<Utc> = guide.metadata["fetched_at"]
            .as_str()
            .unwrap()
            .parse()
            .unwrap();
        assert!(fetched > before);

        let rows = messages(&mut tx, guide.id);
        assert_eq!(rows.len(), 5, "section 0, H1, H2, H3 in two parts");
        for (n, row) in rows.iter().enumerate() {
            assert_eq!(row.role, "document");
            assert_eq!(row.uuid, None, "no uuid: a refetch replaces the page");
            assert_eq!(
                row.at,
                rows[0].at + chrono::Duration::microseconds(n as i64)
            );
            assert!(row.content.chars().count() <= 2_000, "{}", row.content);
        }
        assert!(rows[0].at > before);
        assert!(rows[0].content.contains("Printed for the harbour office."));
        assert!(rows[1].content.starts_with("# Harbour Guide\n"));
        assert!(rows[1].content.contains("The pier opens at dawn."));
        assert!(rows[2].content.starts_with("## Spring tides\n"));
        assert!(
            rows[2].content.contains("    # gauge settings"),
            "a # line in code stays text: {:?}",
            rows[2].content
        );
        assert!(rows[3].content.starts_with("### Neap tides\n"));
        assert!(rows[4].content.starts_with("### Neap tides\n\n"));
        assert!(
            rows[4].content.contains("Red to port."),
            "H4 is not a break"
        );
        assert_eq!(
            rows[3].metadata["heading_path"],
            json!(["Harbour Guide", "Spring tides", "Neap tides"])
        );
        assert_eq!(
            (
                &rows[3].metadata["section_index"],
                &rows[3].metadata["part"]
            ),
            (&json!(3), &json!(0))
        );
        assert_eq!(
            (
                &rows[4].metadata["section_index"],
                &rows[4].metadata["part"]
            ),
            (&json!(3), &json!(1))
        );
        let all: String = rows.iter().map(|r| r.content.as_str()).collect();
        for markup in [
            "Turn on JavaScript",
            "not a heading",
            "navy",
            "<",
            "\u{E000}",
        ] {
            assert!(!all.contains(markup), "{markup:?} in the text");
        }

        let notes = conversation(&mut tx, &urls[2]).expect("the notes are stored");
        assert_eq!(notes.summary, urls[2], "no title: the URL");
        assert_eq!(notes.metadata["content_type"], "text/plain");
        let texts: Vec<String> = messages(&mut tx, notes.id)
            .into_iter()
            .map(|m| m.content)
            .collect();
        assert_eq!(
            texts,
            [
                "# Moorings\nRing 4 is loose.",
                "# Fuel\nDiesel at the north pier."
            ]
        );

        assert!(conversation(&mut tx, &urls[1]).is_none());
        assert_eq!(logged(&mut tx, &urls[1]), [0], "declined, recorded");
        assert!(conversation(&mut tx, &urls[3]).is_none());
        assert!(logged(&mut tx, &urls[3]).is_empty());

        // What went out: GETs with our user agent, no cookie, no credentials.
        let head = server
            .heads()
            .into_iter()
            .find(|h| h.starts_with("GET /guide "))
            .unwrap()
            .to_ascii_lowercase();
        assert!(
            head.contains(&format!("user-agent: {}", USER_AGENT.to_ascii_lowercase())),
            "{head}"
        );
        for h in server.heads() {
            let h = h.to_ascii_lowercase();
            assert!(h.starts_with("get "), "{h}");
            assert!(
                !h.contains("cookie:") && !h.contains("authorization:"),
                "{h}"
            );
        }
        assert_eq!(server.hits("/robots.txt"), 1, "once per host and run");

        // Known URLs are not fetched again; the refused one is tried again.
        let again = run(&mut tx, &source, false, false, &IgnoreRules::default()).unwrap();
        assert_eq!(counts(&again), (4, 0, 3, 1, 0));
        for (path, hits) in [
            ("/guide", 1),
            ("/app", 1),
            ("/notes.txt", 1),
            ("/tides.json", 2),
        ] {
            assert_eq!(server.hits(path), hits, "{path}");
        }
    }

    #[test]
    fn web_refuses_loopback_in_production_policy() {
        let test = "web_refuses_loopback_in_production_policy";
        for ip in ["127.0.0.1", "127.8.9.10", "::1"] {
            let ip: IpAddr = ip.parse().unwrap();
            assert_eq!(refused(ip, Policy::PRODUCTION), Some("loopback"), "{ip}");
            assert_eq!(refused(ip, Policy::allowing_loopback()), None, "{ip}");
        }
        // The test bypass covers plain loopback only.
        let mapped: IpAddr = "::ffff:127.0.0.1".parse().unwrap();
        assert!(refused(mapped, Policy::allowing_loopback()).is_some());
        for ip in ["1.1.1.1", "2606:4700::1111"] {
            assert_eq!(
                refused(ip.parse().unwrap(), Policy::PRODUCTION),
                None,
                "{ip}"
            );
        }

        let server = Server::start(vec![("/guide", Reply::html(&guide_page()))]);
        let port = server.port;
        let urls: Vec<String> = [
            "127.0.0.1",
            "localhost",
            "[::1]",
            "[::ffff:127.0.0.1]",
            "0.0.0.0",
            // 127.0.0.2 in decimal; the parser normalises it.
            "2130706434",
        ]
        .iter()
        .map(|host| format!("http://{host}:{port}/guide"))
        .collect();
        let mut fetcher = Fetcher::new(Policy::PRODUCTION);
        for raw in &urls {
            let err = format!("{:#}", fetcher.fetch(&url(raw)).err().expect(raw));
            assert!(err.starts_with("refused: "), "{raw}: {err}");
        }
        assert!(server.heads().is_empty(), "nothing reached the server");

        let Some(mut client) = test_db() else {
            eprintln!("skipping the store part of {test}: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let (_dir, production) = source_with(&urls, Policy::PRODUCTION);
        let s = run(&mut tx, &production, false, false, &IgnoreRules::default()).unwrap();
        assert_eq!(counts(&s), (6, 0, 0, 6, 0));
        assert!(server.heads().is_empty(), "nothing reached the server");
        for raw in &urls {
            assert!(conversation(&mut tx, url(raw).as_str()).is_none());
        }

        // Negative control: the same server, allowed in the test policy.
        let (_dir, allowed) = source_with(&urls[..1], Policy::allowing_loopback());
        let s = run(&mut tx, &allowed, false, false, &IgnoreRules::default()).unwrap();
        assert_eq!((s.ingested, s.errors), (1, 0));
        assert_eq!(server.hits("/guide"), 1);
    }

    #[test]
    fn web_refuses_private_and_redirect_to_private() {
        let test = "web_refuses_private_and_redirect_to_private";
        let policy = Policy::allowing_loopback();
        for (ip, class) in [
            ("10.0.0.1", "private"),
            ("172.16.5.4", "private"),
            ("172.31.255.255", "private"),
            ("192.168.1.1", "private"),
            ("169.254.169.254", "link-local"),
            ("100.64.0.1", "CGNAT"),
            ("100.127.255.254", "CGNAT"),
            ("0.0.0.0", "0.0.0.0/8"),
            ("0.1.2.3", "0.0.0.0/8"),
            ("224.0.0.1", "multicast"),
            ("255.255.255.255", "reserved"),
            ("fc00::1", "unique-local"),
            ("fd12:3456::1", "unique-local"),
            ("fe80::1", "link-local"),
            ("ff02::1", "multicast"),
            ("::", "unspecified"),
            ("::ffff:10.0.0.1", "IPv4-mapped IPv6"),
            ("::ffff:1.1.1.1", "IPv4-mapped IPv6"),
            ("::10.0.0.1", "IPv4-compatible IPv6"),
        ] {
            assert_eq!(refused(ip.parse().unwrap(), policy), Some(class), "{ip}");
        }
        for ip in ["100.63.255.255", "100.128.0.1", "172.15.0.1", "172.32.0.1"] {
            assert_eq!(refused(ip.parse().unwrap(), policy), None, "{ip}");
        }

        let server = Server::start(vec![
            (
                "/to-private",
                Reply::redirect(302, "http://192.168.1.1/admin"),
            ),
            (
                "/to-metadata",
                Reply::redirect(301, "http://169.254.169.254/latest/meta-data/"),
            ),
            ("/page", Reply::html("<h1>Pier</h1><p>Open.</p>")),
        ]);
        let port = server.port;
        server.set(
            "/to-mapped",
            Reply::redirect(307, &format!("http://[::ffff:127.0.0.1]:{port}/page")),
        );
        // Six redirects to /hop6, five to /ok5.
        for n in 0..6 {
            server.set(
                &format!("/hop{n}"),
                Reply::redirect(302, &format!("/hop{}", n + 1)),
            );
        }
        server.set("/hop6", Reply::html("<h1>Too far</h1><p>Never read.</p>"));
        for n in 0..5 {
            server.set(
                &format!("/ok{n}"),
                Reply::redirect(308, &format!("/ok{}", n + 1)),
            );
        }
        server.set("/ok5", Reply::html("<h1>Reached</h1><p>Five hops.</p>"));

        let mut fetcher = Fetcher::new(policy);
        let started = Instant::now();
        for (raw, class) in [
            ("http://10.0.0.1/".to_string(), "private"),
            ("http://192.168.1.1:8080/".into(), "private"),
            (
                "http://169.254.169.254/latest/meta-data/".into(),
                "link-local",
            ),
            ("http://100.64.0.1/".into(), "CGNAT"),
            ("http://[fc00::1]/".into(), "unique-local"),
            ("http://[fe80::1]/".into(), "link-local"),
            ("http://224.0.0.1/".into(), "multicast"),
            (server.url("/to-private"), "the private address 192.168.1.1"),
            (
                server.url("/to-metadata"),
                "the link-local address 169.254.169.254",
            ),
            (server.url("/to-mapped"), "IPv4-mapped IPv6"),
        ] {
            let err = format!("{:#}", fetcher.fetch(&url(&raw)).err().expect(&raw));
            // Refused by the address check of the hop itself, not later by
            // the robots.txt fetch for that host.
            assert!(
                err.starts_with("refused: ") && err.contains(class) && !err.contains("robots"),
                "{raw}: {err}"
            );
        }
        assert!(
            started.elapsed() < Duration::from_secs(5),
            "refused before any connection: {:?}",
            started.elapsed()
        );
        assert_eq!(
            server.hits("/page"),
            0,
            "the mapped redirect was not followed"
        );

        let err = format!(
            "{:#}",
            fetcher.fetch(&url(&server.url("/hop0"))).err().unwrap()
        );
        assert!(err.contains("more than 5 redirects"), "{err}");
        assert_eq!(server.hits("/hop5"), 1);
        assert_eq!(
            server.hits("/hop6"),
            0,
            "the sixth redirect is not followed"
        );
        let page = fetcher.fetch(&url(&server.url("/ok0"))).unwrap();
        assert_eq!(page.url.path(), "/ok5", "five redirects are followed");

        let Some(mut client) = test_db() else {
            eprintln!("skipping the store part of {test}: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let urls = [
            "http://10.0.0.1/".to_string(),
            server.url("/to-private"),
            server.url("/ok0"),
        ];
        let (_dir, source) = source_with(&urls, policy);
        let s = run(&mut tx, &source, false, false, &IgnoreRules::default()).unwrap();
        assert_eq!(counts(&s), (3, 1, 0, 2, 1));
        let stored = conversation(&mut tx, &urls[2]).unwrap();
        assert_eq!(stored.metadata["final_url"], server.url("/ok5"));
        assert!(conversation(&mut tx, &urls[0]).is_none());
        assert!(conversation(&mut tx, &urls[1]).is_none());
    }

    #[test]
    fn web_strips_credentials_from_the_url() {
        let test = "web_strips_credentials_from_the_url";
        assert_eq!(
            url("http://harbour:tide-secret@127.0.0.1:8080/log?day=3#noon").as_str(),
            "http://127.0.0.1:8080/log?day=3"
        );
        assert_eq!(
            url("https://:only-secret@example.com/a#b").as_str(),
            "https://example.com/a"
        );
        assert_eq!(
            url("https://harbour@example.com/").as_str(),
            "https://example.com/"
        );
        for raw in [
            "ftp://harbour:tide-secret@example.com/",
            "file:///tide-secret",
            "mailto:harbour@tide-secret.example",
            "not a URL tide-secret",
            "http://harbour:tide-secret@/",
        ] {
            let err = clean_url(raw).expect_err(raw);
            assert!(!err.contains("secret"), "{raw}: {err}");
        }

        let server = Server::start(vec![(
            "/log?day=3",
            Reply::html("<title>Log</title><h1>Day 3</h1><p>Calm.</p>"),
        )]);
        let raw = format!(
            "http://harbour:tide-secret@127.0.0.1:{}/log?day=3#noon",
            server.port
        );
        let clean = server.url("/log?day=3");

        // Control: reqwest sends URL credentials as Basic auth on its own.
        let _ = reqwest::blocking::Client::builder()
            .no_proxy()
            .build()
            .unwrap()
            .get(&raw)
            .send();
        let control = server.heads().pop().unwrap().to_ascii_lowercase();
        assert!(control.contains("authorization: basic"), "{control}");

        let Some(mut client) = test_db() else {
            eprintln!("skipping the store part of {test}: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let (_dir, source) = source_with(&[raw], Policy::allowing_loopback());
        let s = run(&mut tx, &source, false, false, &IgnoreRules::default()).unwrap();
        assert_eq!((s.ingested, s.errors), (1, 0));
        let sent = server.heads().pop().unwrap().to_ascii_lowercase();
        assert!(sent.starts_with("get /log?day=3 "), "{sent}");
        assert!(!sent.contains("authorization"), "{sent}");

        let stored = conversation(&mut tx, &clean).expect("keyed by the clean URL");
        assert_eq!(stored.metadata["url"], clean.as_str());
        let leaked: i64 = tx
            .query_one(
                "SELECT (SELECT count(*) FROM conversations WHERE id = $1 \
                   AND (metadata::text || coalesce(summary, '')) ~ '(secret|harbour:|noon)') \
                 + (SELECT count(*) FROM messages WHERE conversation_id = $1 \
                   AND (metadata::text || content) ~ '(secret|harbour:|noon)') \
                 + (SELECT count(*) FROM ingestion_log WHERE file_path ~ '(secret|harbour:|noon)')",
                &[&stored.id],
            )
            .unwrap()
            .get(0);
        assert_eq!(leaked, 0, "no credential or fragment stored");
        assert_eq!(logged(&mut tx, &clean), [1]);
    }

    #[test]
    fn web_respects_robots_disallow() {
        let rules = robots_rules(
            "# harbour rules\n\
             User-agent: *\n\
             Disallow: /\n\
             \n\
             User-agent: Throughline/2.0\n\
             User-agent: other-bot\n\
             Disallow: /logbook/\n\
             Allow: /logbook/public/\n\
             Disallow: /*.pdf$\n\
             Disallow: /a*/c\n\
             Allow: /same\n\
             Disallow: /same\n\
             Sitemap: https://example.invalid/map.xml\n\
             \n\
             User-agent: other-bot\n\
             Disallow: /charts/\n",
        );
        for (path, allowed) in [
            ("/charts/bay", true),
            ("/logbook/day1", false),
            ("/logbook/public/day1", true),
            ("/maps/bay.pdf", false),
            ("/maps/bay.pdf?print=1", true),
            ("/a/b/c", false),
            ("/a/b", true),
            ("/same", true),
            ("/robots.txt", true),
        ] {
            assert_eq!(robots_allows(&rules, path), allowed, "{path}");
        }
        let star = robots_rules("User-agent: *\nDisallow: /private\n");
        assert!(!robots_allows(&star, "/private/x"));
        assert!(robots_allows(&star, "/public"));
        let upper = robots_rules("user-agent: THROUGHLINE\ndisallow: /x\n");
        assert!(!robots_allows(&upper, "/x"));
        let open = robots_rules("User-agent: *\nDisallow:\n");
        assert!(robots_allows(&open, "/anything"));

        let page = || Reply::html("<h1>Chart</h1><p>Bay.</p>");
        let server = Server::start(vec![
            (
                "/robots.txt",
                Reply::typed(
                    "text/plain",
                    "User-agent: *\nDisallow: /\n\nUser-agent: Throughline\n\
                     Disallow: /logbook/\nAllow: /logbook/public/\n",
                ),
            ),
            ("/logbook/private", page()),
            ("/logbook/public/day1", page()),
            ("/charts/bay", page()),
        ]);
        let mut fetcher = Fetcher::new(Policy::allowing_loopback());
        let err = format!(
            "{:#}",
            fetcher
                .fetch(&url(&server.url("/logbook/private")))
                .err()
                .unwrap()
        );
        assert!(
            err.contains("robots.txt disallows /logbook/private"),
            "{err}"
        );
        assert!(fetcher
            .fetch(&url(&server.url("/logbook/public/day1")))
            .is_ok());
        assert!(fetcher.fetch(&url(&server.url("/charts/bay"))).is_ok());
        assert_eq!(server.hits("/logbook/private"), 0);
        assert_eq!(server.hits("/robots.txt"), 1);

        // A 5xx disallows everything.
        let failing = Server::start(vec![
            ("/robots.txt", Reply::status(503)),
            ("/charts/bay", page()),
        ]);
        let err = format!(
            "{:#}",
            fetcher
                .fetch(&url(&failing.url("/charts/bay")))
                .err()
                .unwrap()
        );
        assert!(err.contains("robots.txt unreachable (HTTP 503"), "{err}");
        assert_eq!(failing.targets(), ["/robots.txt"]);

        // A timeout disallows everything and counts toward the budget.
        let slow = Server::start(vec![
            (
                "/robots.txt",
                Reply::typed("text/plain", "").slow(Duration::from_secs(3)),
            ),
            ("/charts/bay", page()),
        ]);
        let mut short = Fetcher::new(Policy::allowing_loopback());
        short.budget = Duration::from_secs(1);
        let started = Instant::now();
        let err = format!(
            "{:#}",
            short.fetch(&url(&slow.url("/charts/bay"))).err().unwrap()
        );
        assert!(err.contains("robots.txt unreachable"), "{err}");
        assert!(started.elapsed() < Duration::from_millis(2_500));
        assert_eq!(slow.hits("/charts/bay"), 0);

        // A 404 allows everything.
        let none = Server::start(vec![("/charts/bay", page())]);
        assert!(fetcher.fetch(&url(&none.url("/charts/bay"))).is_ok());
    }

    #[test]
    fn web_rejects_bodies_over_two_megabytes() {
        let test = "web_rejects_bodies_over_two_megabytes";
        let big = format!("<html><body><p>{}</p>", "y".repeat(3 * 1024 * 1024));
        let (head, tail) = (
            "<html><body><p>Exactly at the cap.</p><!--",
            "--></body></html>",
        );
        let exact = format!(
            "{head}{}{tail}",
            "x".repeat(MAX_BODY_BYTES as usize - head.len() - tail.len())
        );
        assert_eq!(exact.len() as u64, MAX_BODY_BYTES);
        let server = Server::start(vec![
            ("/big", Reply::html(&big).streamed()),
            ("/big-declared", Reply::html(&big)),
            ("/exact", Reply::html(&exact).streamed()),
        ]);
        let mut fetcher = Fetcher::new(Policy::allowing_loopback());
        for path in ["/big", "/big-declared"] {
            let err = format!(
                "{:#}",
                fetcher.fetch(&url(&server.url(path))).err().unwrap()
            );
            assert!(err.contains("larger than 2 MiB"), "{path}: {err}");
        }
        let page = fetcher.fetch(&url(&server.url("/exact"))).unwrap();
        assert_eq!(
            page.body.len() as u64,
            MAX_BODY_BYTES,
            "the cap itself is read"
        );

        let Some(mut client) = test_db() else {
            eprintln!("skipping the store part of {test}: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let urls = ["/big", "/big-declared", "/exact"].map(|p| server.url(p));
        let (_dir, source) = source_with(&urls, Policy::allowing_loopback());
        let s = run(&mut tx, &source, false, false, &IgnoreRules::default()).unwrap();
        assert_eq!(counts(&s), (3, 1, 0, 2, 1));
        assert!(conversation(&mut tx, &urls[0]).is_none());
        assert!(conversation(&mut tx, &urls[1]).is_none());
        let exact = conversation(&mut tx, &urls[2]).unwrap();
        let rows = messages(&mut tx, exact.id);
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].content, "Exactly at the cap.");
    }

    #[test]
    fn web_refetch_replaces_the_page() {
        let test = "web_refetch_replaces_the_page";
        let v1 = "<html><head><title>Tide log v1</title></head><body><h1>Tide log</h1>\
                  <p>High water at 06:10.</p><h2>Wind</h2><p>Light from the west.</p></body></html>";
        let v2 = "<html><head><title>Tide log v2</title></head><body><h1>Tide log</h1>\
                  <p>High water at 06:40.</p><h2>Wind</h2><p>Strong from the north.</p>\
                  <h2>Swell</h2><p>Two metres.</p></body></html>";
        let server = Server::start(vec![("/log", Reply::html(v1))]);
        let page = server.url("/log");
        let (_dir, source) = source_with(std::slice::from_ref(&page), Policy::allowing_loopback());
        let Some(mut client) = test_db() else {
            eprintln!("skipping {test}: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let rules = IgnoreRules::default();

        let first = run(&mut tx, &source, false, false, &rules).unwrap();
        assert_eq!(counts(&first), (1, 1, 0, 0, 2));
        let conv = conversation(&mut tx, &page).unwrap();
        let old = messages(&mut tx, conv.id);
        let old_fetch = conv.metadata["fetched_at"].clone();

        // Without --refetch a known page is not fetched, changed or not.
        server.set("/log", Reply::html(v2));
        let second = run(&mut tx, &source, false, false, &rules).unwrap();
        assert_eq!(counts(&second), (1, 0, 1, 0, 0));
        assert_eq!(server.hits("/log"), 1);
        let kept: Vec<i64> = messages(&mut tx, conv.id).iter().map(|m| m.id).collect();
        assert_eq!(kept, old.iter().map(|m| m.id).collect::<Vec<_>>());

        // With --refetch the new version replaces the old one.
        let third = run(&mut tx, &source, true, false, &rules).unwrap();
        assert_eq!(counts(&third), (1, 1, 0, 0, 3));
        assert_eq!(third.refreshed, 1);
        assert_eq!(server.hits("/log"), 2);
        let after = conversation(&mut tx, &page).unwrap();
        assert_eq!(after.id, conv.id, "the same conversation");
        assert_eq!(after.summary, "Tide log v2");
        assert_ne!(after.metadata["fetched_at"], old_fetch);
        let new = messages(&mut tx, conv.id);
        assert_eq!(new.len(), 3);
        assert!(new.iter().all(|m| old.iter().all(|o| o.id != m.id)));
        let text: String = new.iter().map(|m| m.content.as_str()).collect();
        assert!(text.contains("06:40") && text.contains("Two metres."));
        assert!(!text.contains("06:10") && !text.contains("Light from the west"));
        let old_last = old.iter().map(|m| m.at).max().unwrap();
        assert!(new[0].at > old_last, "the new version starts after the old");

        // A refetch of an unchanged page writes nothing.
        let fourth = run(&mut tx, &source, true, false, &rules).unwrap();
        assert_eq!(counts(&fourth), (1, 0, 1, 0, 0));
        assert_eq!(server.hits("/log"), 3);
        let same: Vec<i64> = messages(&mut tx, conv.id).iter().map(|m| m.id).collect();
        assert_eq!(same, new.iter().map(|m| m.id).collect::<Vec<_>>());
        assert_eq!(logged(&mut tx, &page), [2, 3]);
    }

    #[test]
    fn web_without_url_file_is_a_noop() {
        let test = "web_without_url_file_is_a_noop";
        if std::env::var_os(URLS_ENV).is_none() {
            assert_eq!(WebSource::from_env().url_file(), None);
        }
        let dir = tempfile::tempdir().unwrap();
        let comments = dir.path().join("comments.txt");
        std::fs::write(
            &comments,
            "# tide pages\n\n   \n# http://example.invalid/\n",
        )
        .unwrap();
        let two = dir.path().join("two.txt");
        std::fs::write(
            &two,
            "https://example.invalid/a\n# skip\nhttps://example.invalid/b\n",
        )
        .unwrap();
        let one = dir.path().join("one.txt");
        std::fs::write(&one, "https://example.invalid/a\n").unwrap();
        let none = WebSource::new(None);
        let missing = WebSource::new(Some(dir.path().join("absent.txt")));
        let only_comments = WebSource::new(Some(comments));
        assert_eq!(doctor_line(&none), "web: 0 urls configured");
        assert_eq!(
            doctor_line(&missing),
            "web: 0 urls configured, URL file not readable"
        );
        assert_eq!(doctor_line(&only_comments), "web: 0 urls configured");
        assert_eq!(
            doctor_line(&WebSource::new(Some(one))),
            "web: 1 url configured"
        );
        assert_eq!(
            doctor_line(&WebSource::new(Some(two))),
            "web: 2 urls configured"
        );

        let Some(mut client) = test_db() else {
            eprintln!("skipping the store part of {test}: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let rows = |tx: &mut postgres::Transaction<'_>| -> (i64, i64) {
            let r = tx
                .query_one(
                    "SELECT (SELECT count(*) FROM conversations), \
                     (SELECT count(*) FROM ingestion_log)",
                    &[],
                )
                .unwrap();
            (r.get(0), r.get(1))
        };
        let before = rows(&mut tx);
        for source in [&none, &missing, &only_comments] {
            let s = run(&mut tx, source, true, true, &IgnoreRules::default()).unwrap();
            assert_eq!(counts(&s), (0, 0, 0, 0, 0));
        }
        assert_eq!(rows(&mut tx), before, "nothing written");
    }

    /// The production path, by hand inside the container:
    /// `cargo test -p throughline web_live_public_page -- --ignored`.
    #[test]
    #[ignore = "fetches a public page over the network; run by hand"]
    fn web_live_public_page() {
        let live = url("https://example.com/");
        let mut fetcher = Fetcher::new(Policy::PRODUCTION);
        let page = fetcher
            .fetch(&live)
            .unwrap_or_else(|e| panic!("live fetch of {live} failed: {e:#}"));
        let (title, sections) = sections_of(&page).unwrap();
        eprintln!(
            "live: {} -> {} status {} type {} etag {:?} bytes {} title {:?} sections {}",
            live,
            page.url,
            page.status,
            page.content_type,
            page.etag,
            page.body.len(),
            title,
            sections.len()
        );
        assert_eq!(page.status, 200);
        assert_eq!(page.content_type, "text/html");
        assert!(!sections.is_empty());

        let Some(mut client) = test_db() else {
            eprintln!("skipping the store part of web_live_public_page: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let (_dir, source) = source_with(&[live.to_string()], Policy::PRODUCTION);
        let s = run(&mut tx, &source, false, true, &IgnoreRules::default()).unwrap();
        eprintln!("live store: {:?}", counts(&s));
        assert_eq!((s.ingested, s.errors), (1, 0));
    }
}
