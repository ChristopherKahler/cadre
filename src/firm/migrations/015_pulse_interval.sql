-- 015: the pulse interval gets its own column (#134).
--
-- firm.schedule (003) holds the firm's business hours. Until now
-- `heartbeat enable` and `doctor --fix` also wrote the pulse interval into it
-- ("30m") and `heartbeat disable` wrote NULL, and the business-hours gate reads
-- either value as "always open". The interval lives here from now on, and
-- schedule means business hours only.

ALTER TABLE firm ADD COLUMN pulse_interval TEXT;

-- A firm enabled before this migration holds its interval in schedule. Copy
-- it across and leave schedule exactly as it is: the hours it replaced cannot
-- be read back, because nothing keeps a field's history, so there is nothing
-- to restore. `firm doctor` tells the Board instead (the business-hours card).
--
-- "An interval" is the grammar heartbeat enable validates with
-- (sched/base.py): digits, then s, m, min, h or d, once surrounding
-- whitespace is stripped. SQLite has no regex, so the test is spelled out in
-- two steps: copy the stripped value, then clear every copy that is not a
-- unit suffix with only digits, at least one, in front of it. Only ASCII
-- whitespace is stripped here, where Python strips more. A value padded with
-- other whitespace is not copied; the doctor's timer card then reports a
-- running timer with no interval, and `doctor --fix` fills the column from it.

UPDATE firm
   SET pulse_interval = trim(schedule, ' ' || char(9, 10, 11, 12, 13))
 WHERE schedule IS NOT NULL;

UPDATE firm
   SET pulse_interval = NULL
 WHERE pulse_interval IS NOT NULL
   AND NOT (
        (substr(pulse_interval, -3) = 'min'
         AND length(pulse_interval) > 3
         AND substr(pulse_interval, 1, length(pulse_interval) - 3) NOT GLOB '*[^0-9]*')
     OR (substr(pulse_interval, -1) IN ('s', 'm', 'h', 'd')
         AND length(pulse_interval) > 1
         AND substr(pulse_interval, 1, length(pulse_interval) - 1) NOT GLOB '*[^0-9]*')
   );
