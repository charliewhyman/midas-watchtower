"""Logging configuration for AI Safety Monitor"""
import sys
import logging
from pathlib import Path
from typing import Optional


def setup_logging(log_level: str = "INFO", log_file: Optional[str] = None) -> logging.Logger:
    """
    Comprehensive logging setup
    
    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional path to log file
    
    Returns:
        Configured logger instance
    """
    # Create logs directory if needed (create parents and ignore if exists)
    if log_file:
        log_path = Path(log_file)
    else:
        log_path = Path("logs/monitor.log")
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        # If we can't create the directory (e.g., permissions), we'll
        # attempt to continue and let the FileHandler raise when opened.
        pass
    
    # Get root logger
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, log_level.upper()))
    
    # Clear any existing handlers
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    # Formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
    )
    
    # File handler (best-effort). If creating/opening the file fails
    # (permissions, mount issues), fall back to console logging only.
    try:
        file_handler = logging.FileHandler(log_path)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except (PermissionError, OSError) as e:
        # Add a stream handler so messages still appear, and warn
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
        # Use a temporary logger to emit a clear warning to stdout
        temp_logger = logging.getLogger("logging_setup_fallback")
        temp_logger.setLevel(getattr(logging, log_level.upper()))
        if not temp_logger.handlers:
            temp_logger.addHandler(stream_handler)
        temp_logger.warning(
            f"Cannot write log file '{log_path}': {e}. Falling back to stdout/stderr."
        )
    else:
        # Stream handler (console) in addition to file logging
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
    
    # Set specific log levels for noisy libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("gspread").setLevel(logging.INFO)
    
    logger.info(f"Logging initialized (level: {log_level}, file: {log_path})")
    
    return logger