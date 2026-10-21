from pathlib import Path

p = Path("rust/crates/throughline/src/migrate.rs")
s = p.read_text(encoding="utf-8")
if "003_token_bigint" not in s:
    old = '''const MIGRATIONS: &[(&str, &str)] = &[
    ("001_rust_core.sql", CORE_SCHEMA),
    ("002_uuid_arbiter.sql", UUID_ARBITER),
];'''
    new = '''const TOKEN_BIGINT: &str = r#"
ALTER TABLE conversations ALTER COLUMN token_count_in TYPE BIGINT;
ALTER TABLE conversations ALTER COLUMN token_count_out TYPE BIGINT;
"#;

/// Migrations the Rust CLI owns. The Python package has its own numbered
/// files; both share `applied_migrations` so a mixed-history database works.
const MIGRATIONS: &[(&str, &str)] = &[
    ("001_rust_core.sql", CORE_SCHEMA),
    ("002_uuid_arbiter.sql", UUID_ARBITER),
    ("003_token_bigint.sql", TOKEN_BIGINT),
];'''
    if old in s:
        s = s.replace(old, new)
        p.write_text(s, encoding="utf-8")
        print("added 003")
    else:
        print("MIGRATIONS block not found")
else:
    print("003 exists")
