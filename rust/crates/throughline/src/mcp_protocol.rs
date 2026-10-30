//! Protocol constants and version negotiation shared by every MCP transport.

/// The modern protocol era (`_meta`-based, `resultType`).
pub const MODERN_VERSION: &str = "2026-07-28";
/// Legacy versions served on every transport, newest first.
pub const LEGACY_VERSIONS: [&str; 2] = ["2025-11-25", "2025-06-18"];
/// A legacy version that only stdio still answers.
pub const STDIO_EXTRA_VERSION: &str = "2024-11-05";

pub const HEADER_MISMATCH: i64 = -32020;
pub const UNSUPPORTED_VERSION: i64 = -32022;
pub const METHOD_NOT_FOUND: i64 = -32601;
pub const INVALID_PARAMS: i64 = -32602;
pub const RESOURCE_NOT_FOUND: i64 = -32002;
pub const PARSE_ERROR: i64 = -32700;
pub const INVALID_REQUEST: i64 = -32600;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Transport {
    Stdio,
    Http,
}

/// The legacy version to answer `initialize` with: the requested one when
/// this transport supports it, otherwise the newest legacy version.
pub fn negotiate_legacy(requested: Option<&str>, transport: Transport) -> &'static str {
    let supported = LEGACY_VERSIONS
        .iter()
        .copied()
        .chain((transport == Transport::Stdio).then_some(STDIO_EXTRA_VERSION));
    requested
        .and_then(|r| supported.into_iter().find(|v| *v == r))
        .unwrap_or(LEGACY_VERSIONS[0])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn negotiate_keeps_supported_versions() {
        for t in [Transport::Stdio, Transport::Http] {
            for v in ["2025-06-18", "2025-11-25"] {
                assert_eq!(negotiate_legacy(Some(v), t), v);
            }
        }
    }

    #[test]
    fn negotiate_falls_back_to_latest() {
        for t in [Transport::Stdio, Transport::Http] {
            for v in [Some("2025-03-26"), None, Some("x")] {
                assert_eq!(negotiate_legacy(v, t), "2025-11-25");
            }
        }
    }

    #[test]
    fn stdio_keeps_2024_11_05() {
        assert_eq!(
            negotiate_legacy(Some("2024-11-05"), Transport::Stdio),
            "2024-11-05"
        );
        assert_eq!(
            negotiate_legacy(Some("2024-11-05"), Transport::Http),
            "2025-11-25"
        );
    }
}
