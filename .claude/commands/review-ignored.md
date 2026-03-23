Query the database to show recent entries that were categorized as "ignore" so the user can review whether the AI is filtering correctly.

1. Query `message_mapping` for recent entries where `category = 'ignore'`, ordered by timestamp descending, limit 15
2. For each entry, show:
   - Entry ID (and source type: twitter/telegram)
   - A preview of the content (first 150 chars)
   - The AI's reasoning for categorizing as ignore
   - The timestamp
3. Summarize: how many entries went to ignore in the last 24h vs total entries processed

This helps the user spot-check whether the AI is being too aggressive with filtering.
