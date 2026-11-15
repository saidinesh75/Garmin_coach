# Garmin API mapping — checked 14 November 2026

Primary references: [Personal API](https://developer.garmin.com/gc-developer-program/personal/personal-api/), [FAQ](https://developer.garmin.com/gc-developer-program/personal/personal-faq/), [Swagger](https://developer.garmin.com/gc-developer-program/personal/swagger-docs), [official Python client](https://github.com/garmin/connect-personal-api-python). Global resource origin: `https://personal.connectapi.garmin.com`. Authentication: raw JWT bearer obtained by an API-key-authenticated token POST; see README. No business API client ID/secret or refresh-token endpoint is used.

| Resource | GET path / parameters | Normalized fields / units |
| --- | --- | --- |
| Daily summary | `/usersummary-service/usersummary/daily?calendarDate=YYYY-MM-DD` | `totalSteps` → steps; `restingHeartRate` → bpm; `averageStressLevel` → 0–100; `bodyBatteryAtWakeTime`, `bodyBatteryHighestValue`, `bodyBatteryLowestValue`, `bodyBatteryMostRecentValue` → 0–100; charged/drained totals; intensity minutes; active kcal |
| Sleep | `/sleep-service/sleep/dailySleep?date=YYYY-MM-DD` | `sleepTimeSeconds` / 3600 → hours; `sleepScores.overall.value` → score; deep/light/REM/awake/nap seconds |
| HRV | `/hrv-service/hrv/{calendarDate}` | `hrvSummary.lastNightAvg` → ms; `hrvSummary.status` → Garmin enum; no use of weekly average as a nightly measurement |
| Heart rate | `/wellness-service/wellness/dailyHeartRate?date=YYYY-MM-DD` | `minHeartRate`, `maxHeartRate` → bpm; full series retained raw; resting HR comes from summary |
| Body Battery report | `/wellness-service/wellness/bodyBattery/reports/daily?startDate=YYYY-MM-DD&endDate=YYYY-MM-DD` | Intraday report preserved raw with its column descriptors; dashboard uses summary wake/high/low fields rather than inferring wake from first sample |
| Activities | `/activitylist-service/activities/search/activities?startDate=...&endDate=...&start=0&limit=100&sortOrder=asc&excludeChildren=true` | `activityId`; `startTimeLocal` calendar date; duration seconds; distance meters; `averageHR` bpm; aerobic/anaerobic effects; `activityTrainingLoad` when supplied |
| Structured workouts attached to activity | `/activity-service/activity/{activityId}/workouts` | Workout JSON associated with activity and source ID; targets preserved with their type identifiers |

Only these documented routes are used. The Swagger snapshot also documents range endpoints for sleep, HRV and daily summaries, activity details, downloads and other resources. They are not required for this first implementation. Per-date requests make checkpoint ownership and no-data tracking straightforward at the cost of more requests.

No published numeric rate limit or historical retention guarantee was established from the references consulted. The one-second pacing and one-hour minimum cooldown are **app policies**, not Garmin entitlements. No-data/204 stays empty. 404 is a visible retryable resource error. Authentication failures stop the job. An API key's expiry and a token's expiry are different from availability of previously recorded history.

Data sources have been checked at the documentation/contract level. Real-account runtime verification remains pending secure local configuration. The bundled OpenAPI contract is not a live guarantee: use the raw evidence and visible schema-error checkpoints when Garmin changes a response shape.
