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
    fn date_only_today_is_never_future() {
        // heute in UTC+14 ist das späteste „heute“ irgendwo auf der Welt
        let today = (chrono::Utc::now() + chrono::Duration::hours(14))
            .format("%Y-%m-%d")
            .to_string();
        assert!(parse_instant(&today).unwrap() <= chrono::Utc::now());
    }
}
