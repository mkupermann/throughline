//! Valid time of memory notes: parsing of reference dates.

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
