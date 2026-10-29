//! Valid time of memory notes: parsing of reference dates and the as-of predicate.

use chrono::{DateTime, Duration, NaiveDate, Utc};

/// Parse a reference date: RFC 3339, or `YYYY-MM-DD` (midnight UTC).
///
/// A plain date that lies after `Utc::now()` but is not after today's date in
/// UTC+14 is somebody's "today" and becomes `Utc::now()`; a date beyond that
/// stays in the future.
pub fn parse_instant(s: &str) -> Option<DateTime<Utc>> {
    let s = s.trim();
    if let Ok(t) = DateTime::parse_from_rfc3339(s) {
        return Some(t.with_timezone(&Utc));
    }
    let date = NaiveDate::parse_from_str(s, "%Y-%m-%d").ok()?;
    let midnight = date.and_hms_opt(0, 0, 0)?.and_utc();
    let now = Utc::now();
    if midnight > now && date <= (now + Duration::hours(14)).date_naive() {
        return Some(now);
    }
    Some(midnight)
}

/// `true` for a plain `YYYY-MM-DD` without a time of day.
pub fn is_plain_date(s: &str) -> bool {
    NaiveDate::parse_from_str(s.trim(), "%Y-%m-%d").is_ok()
}

/// Parse the reference date of an as-of question (`valid_at`, `known_at`).
/// RFC 3339 passes through unchanged. A plain `YYYY-MM-DD` means the end of
/// that day in UTC, capped at now, so "as of today" includes today's changes.
pub fn parse_as_of_instant(s: &str) -> Option<DateTime<Utc>> {
    let s = s.trim();
    if let Ok(t) = DateTime::parse_from_rfc3339(s) {
        return Some(t.with_timezone(&Utc));
    }
    let date = NaiveDate::parse_from_str(s, "%Y-%m-%d").ok()?;
    let end = date.succ_opt()?.and_hms_opt(0, 0, 0)?.and_utc() - Duration::microseconds(1);
    Some(end.min(Utc::now()))
}

/// `$` followed by one or more ASCII digits.
fn is_placeholder(p: &str) -> bool {
    p.strip_prefix('$')
        .is_some_and(|n| !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()))
}

/// SQL predicate (table alias `mc`) for "what was valid on `valid_at`, as known
/// on `known_at`". The arguments are placeholders such as `"$6"` and `"$7"`,
/// bound as `timestamptz`; either may be NULL (no restriction on that axis).
///
/// A note is visible at `known_at` from its creation until it is superseded.
/// It holds on `valid_at` from `valid_from` (else `source_at`, else
/// `created_at`) until the earliest of `valid_to` and `expires_at`. `valid_to`
/// counts only once the replacement was known: before the supersession the old
/// note was still open-ended.
pub fn as_of_predicate(valid_at: &str, known_at: &str) -> String {
    // The arguments are spliced into SQL: only `$n` placeholders belong here.
    debug_assert!(
        is_placeholder(valid_at) && is_placeholder(known_at),
        "as_of_predicate takes $n placeholders, got {valid_at:?}, {known_at:?}"
    );
    let (v, k) = (valid_at, known_at);
    format!(
        "COALESCE(mc.status, 'active') <> 'forgotten'
         AND ({k}::timestamptz IS NULL
              OR (mc.created_at <= {k} AND (mc.superseded_at IS NULL OR {k} < mc.superseded_at)))
         AND ({v}::timestamptz IS NULL
              OR (COALESCE(mc.valid_from, mc.source_at, mc.created_at) <= {v}
                  AND {v} < COALESCE(LEAST(
                        CASE WHEN mc.superseded_at IS NULL OR COALESCE({k}, now()) >= mc.superseded_at
                             THEN mc.valid_to END,
                        mc.expires_at), 'infinity')))"
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_rfc3339_and_plain_dates() {
        assert_eq!(
            parse_instant("2026-03-01").unwrap().to_rfc3339(),
            "2026-03-01T00:00:00+00:00"
        );
        assert!(parse_instant("2026-03-01T10:00:00+01:00").is_some());
        assert!(parse_instant("gestern").is_none());
        assert!(parse_instant("2026-02-30").is_none());
    }

    #[test]
    fn as_of_plain_date_is_the_end_of_that_day() {
        assert_eq!(
            parse_as_of_instant("2026-03-01").unwrap().to_rfc3339(),
            "2026-03-01T23:59:59.999999+00:00"
        );
        assert_eq!(
            parse_as_of_instant("2026-03-01T10:00:00+01:00")
                .unwrap()
                .to_rfc3339(),
            "2026-03-01T09:00:00+00:00",
            "RFC 3339 passes through unchanged"
        );
        assert!(parse_as_of_instant("gestern").is_none());
        assert!(parse_as_of_instant("2026-02-30").is_none());
        let now = chrono::Utc::now();
        let today = parse_as_of_instant(&now.format("%Y-%m-%d").to_string()).unwrap();
        assert!(today >= now, "today includes the time up to now");
        assert!(today <= chrono::Utc::now(), "capped at now");
        // supersede keeps the start of the day
        assert_eq!(
            parse_instant("2026-03-01").unwrap().to_rfc3339(),
            "2026-03-01T00:00:00+00:00"
        );
    }

    #[test]
    fn placeholders_are_dollar_and_digits() {
        assert!(as_of_predicate("$6", "$12").contains("$12"));
        assert!(is_placeholder("$1") && is_placeholder("$12"));
        for bad in ["now()", "$", "$2a", "1", "", " $1", "$1 "] {
            assert!(!is_placeholder(bad), "{bad:?}");
        }
    }

    /// The one expected panic of the suite: the assertion is wired in.
    #[test]
    #[cfg(debug_assertions)]
    #[should_panic(expected = "as_of_predicate takes $n placeholders")]
    fn as_of_predicate_refuses_anything_but_placeholders() {
        as_of_predicate("now()", "$2");
    }

    #[test]
    fn tells_a_plain_date_from_a_timestamp() {
        assert!(is_plain_date("2026-03-01"));
        assert!(is_plain_date(" 2026-03-01 "));
        assert!(!is_plain_date("2026-03-01T00:00:00Z"));
        assert!(!is_plain_date("2026-02-30"));
        assert!(!is_plain_date("gestern"));
    }

    #[test]
    fn date_only_today_is_never_future() {
        // heute in UTC+14 ist das späteste „heute“ irgendwo auf der Welt
        let today = (chrono::Utc::now() + chrono::Duration::hours(14))
            .format("%Y-%m-%d")
            .to_string();
        assert!(parse_instant(&today).unwrap() <= chrono::Utc::now());
    }
}
