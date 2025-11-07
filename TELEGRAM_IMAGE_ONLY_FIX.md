# Telegram Image-Only Entry Processing Fix

## Problem
Image-only Telegram messages (messages with images but no text) were being rejected before processing because:

1. The bot checked for content before downloading media
2. OCR text extraction happened AFTER the content validation check
3. Result: Image-only entries were skipped with "No content to process" error

## Solution
Modified the `process_entry()` method in `main.py` to handle image-only Telegram entries:

### Changes Made

1. **Early Detection and Processing** (Lines 99-116)
   - Detect image-only Telegram entries before content validation
   - Download media and run OCR BEFORE the content check
   - Use OCR extracted text as the content
   - If OCR fails or is disabled, use a placeholder: `[Image content - no text extracted]`

2. **Avoid Re-downloading Media** (Lines 157-172)
   - Check if `media_files` already exists before downloading
   - Skip media download for entries that were already processed (image-only entries)
   - Prevents duplicate downloads and processing time

### Flow for Image-Only Telegram Entries

```
1. Telegram message arrives with image but no text
2. Bot detects: source_type='telegram' + no content + has_media
3. Download media immediately
4. Run OCR to extract text from image
5. Set content = OCR text (or placeholder if OCR fails)
6. Continue with normal processing:
   - Generate embedding for duplicate detection
   - Check for duplicates
   - Categorize content
   - Post to Discord
7. Skip media download step (already done)
8. Post images with content to Discord
```

### Supported Cases

| Case | Behavior |
|------|----------|
| Image with text | Normal processing flow |
| Image without text + OCR enabled | Use OCR text as content |
| Image without text + OCR disabled | Use placeholder as content |
| Image without text + OCR fails | Use placeholder as content |
| Album with text | Normal processing flow |
| Album without text (image-only album) | Downloads all images, combines OCR text from all images |
| No media at all | Skip (as before) |

## Benefits

1. **Image-only posts are now processed** - Previously rejected entries will now be posted to Discord
2. **Better content extraction** - OCR text can be used for categorization and duplicate detection
3. **Efficient processing** - Avoids re-downloading media that was already downloaded
4. **Graceful fallbacks** - Uses placeholders when OCR is unavailable
5. **No breaking changes** - Existing functionality for text-based and mixed content entries remains unchanged

## Testing

To test this fix:

**Single Image Test:**
1. Send an image-only message (no text) to a monitored Telegram channel
2. Check the bot logs for: "Image-only Telegram entry detected, downloading media for OCR..."
3. Verify the message is posted to Discord with either OCR-extracted text or placeholder
4. Confirm proper categorization based on image content

**Album Test:**
1. Send multiple images as an album (no text) to a monitored Telegram channel
2. Bot should detect it as image-only and download all album media
3. OCR text should be extracted and combined from all images in the album
4. Verify all images are posted to Discord together with the combined OCR text

## Configuration

OCR can be configured in `config.py`:
- `OCR_ENABLED` - Enable/disable OCR (default: True)
- `TESSERACT_PATH` - Custom Tesseract installation path
- `OCR_LANGUAGE` - OCR language (default: 'eng')

