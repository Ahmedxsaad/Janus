-- The calendar the dbt semantic layer requires.
--
-- Not a feature table and nothing trains on it: dbt refuses to parse a project
-- that declares a semantic model without a time spine, so any project with a
-- semantic layer has one of these. It is here for the same reason it is in
-- theirs, and it is a table ModelGuard reads past rather than one it reads.
{{ dbt.date_spine('day', "cast('2020-01-01' as date)", "cast('2027-01-01' as date)") }}
