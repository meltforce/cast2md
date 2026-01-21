# Lesson Learned: Premium Podcast Signed/Expiring Audio URLs

**Date:** 2026-01-21
**Category:** Download Failures
**Affected Feeds:** Premium/Member podcast feeds (e.g., FoundMyFitness Member's Feed)

## Problem

Premium podcast feeds often use **signed/expiring URLs** for audio files instead of permanent URLs. These URLs contain authentication tokens or signatures that expire after a certain time period.

### Symptoms

- Episodes fail with error: "Failed to download audio"
- Downloads worked initially but fail on retry
- Large batches of episodes from the same premium feed all fail
- Audio URLs contain encoded tokens (e.g., Rails Active Storage blob URLs)

### Example of Expiring URL

```
http://foundmyfitness.com/rails/active_storage/blobs/redirect/
eyJfcmFpbHMiOnsibWVzc2FnZSI6IkJBaHBBbWtFIiwiZXhwIjpudWxsLCJwdXIiOiJibG9iX2lkIn19
--cbfcb8fc24123ce6bbdd7a4404aee06510ccdc85/filename.mp3
```

The base64-encoded portion contains a signature that expires.

## Root Cause

cast2md originally stored the audio URL when an episode was first discovered from the RSS feed. If the download was delayed (e.g., queued behind other downloads, server restart, network issues), the stored URL would expire and become invalid.

## Solution

Modified `download_episode()` to **refresh the audio URL from the feed** before downloading:

1. Fetch the current RSS feed
2. Find the episode by GUID
3. Extract the fresh audio URL
4. Use the fresh URL for downloading
5. Update the stored URL in the database

### Implementation

- `refresh_audio_url_from_feed(feed, episode_guid)` - Fetches feed and returns fresh URL
- `download_episode()` - Now calls refresh function before downloading
- `EpisodeRepository.update_audio_url()` - Persists the refreshed URL

### Code Location

- `src/cast2md/download/downloader.py`
- `src/cast2md/db/repository.py`

## Impact

- **111 episodes** from FoundMyFitness Member's Feed were affected
- Fix applies to all future downloads automatically
- Existing failed episodes can be reset to "pending" and requeued

## Recovery Steps

To retry failed downloads after the fix:

```sql
-- Reset failed download episodes to pending
UPDATE episode
SET status='pending', error_message=NULL
WHERE status='failed' AND error_message='Failed to download audio';
```

Then click "Queue All Pending" in the web UI.

## Prevention

This fix is now permanent and handles expiring URLs automatically. No additional configuration needed for premium podcast feeds.

## Affected Podcast Platforms

Known platforms that use signed/expiring URLs:

- **Memberful** (used by FoundMyFitness)
- **Rails Active Storage** based platforms
- **Patreon** premium feeds
- Other membership platforms with private RSS feeds

## Performance Consideration

The fix adds one HTTP request per download (to fetch the feed). This is acceptable because:

1. Downloads are already network-bound operations
2. Feed fetches are cached by most podcast platforms
3. The alternative (failed downloads) is much worse

For feeds with permanent URLs, the refresh is still performed but the URL typically remains unchanged.
