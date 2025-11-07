"""
OCR handler for extracting text from images using Tesseract
"""
import os
import pytesseract
from PIL import Image
from utils import logger
import config

class OCRHandler:
    """Handles OCR text extraction from images"""
    
    def __init__(self):
        """Initialize OCR handler and configure Tesseract path"""
        # Set Tesseract executable path for Windows
        # Try standard Windows installation path first
        tesseract_paths = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ]
        
        # Check if custom path is specified in config
        if hasattr(config, 'TESSERACT_PATH') and config.TESSERACT_PATH:
            tesseract_paths.insert(0, config.TESSERACT_PATH)
        
        # Find the first existing Tesseract installation
        tesseract_found = False
        for path in tesseract_paths:
            if os.path.exists(path):
                pytesseract.pytesseract.tesseract_cmd = path
                tesseract_found = True
                logger.info(f"Tesseract OCR found at: {path}")
                break
        
        if not tesseract_found:
            logger.warning(
                f"Tesseract not found in standard locations. "
                f"OCR will be disabled unless Tesseract is in PATH or TESSERACT_PATH is set in config.py"
            )
        
        self.enabled = tesseract_found and getattr(config, 'OCR_ENABLED', True)
        self.language = getattr(config, 'OCR_LANGUAGE', 'eng')
        
        if self.enabled:
            logger.info(f"OCR Handler initialized (Language: {self.language})")
        else:
            logger.info("OCR Handler disabled")
    
    def extract_text_from_image(self, image_path):
        """
        Extract text from an image file using OCR
        
        Args:
            image_path: Path to the image file
        
        Returns:
            str: Extracted text, or empty string if extraction fails
        """
        if not self.enabled:
            return ""
        
        if not os.path.exists(image_path):
            logger.warning(f"Image file not found: {image_path}")
            return ""
        
        try:
            # Open the image
            image = Image.open(image_path)
            
            # Perform OCR
            text = pytesseract.image_to_string(image, lang=self.language)
            
            # Clean up the extracted text
            text = text.strip()
            
            if text:
                logger.debug(f"OCR extracted {len(text)} characters from {os.path.basename(image_path)}")
            else:
                logger.debug(f"No text found in {os.path.basename(image_path)}")
            
            return text
            
        except Exception as e:
            logger.error(f"Error extracting text from {image_path}: {e}")
            return ""
    
    def extract_text_from_images(self, image_paths):
        """
        Extract text from multiple image files
        
        Args:
            image_paths: List of paths to image files
        
        Returns:
            str: Combined extracted text from all images
        """
        if not self.enabled:
            return ""
        
        if not image_paths:
            return ""
        
        extracted_texts = []
        
        for image_path in image_paths:
            text = self.extract_text_from_image(image_path)
            if text:
                extracted_texts.append(text)
        
        # Combine all extracted texts
        combined_text = "\n\n".join(extracted_texts)
        
        if combined_text:
            logger.info(f"OCR extracted total of {len(combined_text)} characters from {len(image_paths)} images")
        
        return combined_text


