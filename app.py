"""FastAPI application for AI Safety Metadata Monitor"""
import os
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
import uvicorn

from monitoring_service import MonitoringService
from config import ConfigurationError
import logging

logger = logging.getLogger(__name__)

# Global application state
app = FastAPI(
    title="AI Safety Metadata Monitor",
    description="Monitor AI safety policies and research for changes",
    version="2.0.0"
)

# Global service instance
_monitor_service = None
_executor = ThreadPoolExecutor(max_workers=1)  # Single worker for monitoring cycles


def get_monitor_service() -> MonitoringService:
    """Dependency to get the monitoring service instance"""
    global _monitor_service
    if _monitor_service is None:
        try:
            _monitor_service = MonitoringService()
        except ConfigurationError as e:
            logger.error(f"Configuration error: {e}")
            raise HTTPException(status_code=500, detail=f"Configuration error: {e}")
        except (RuntimeError, OSError, ValueError, TypeError) as e:
            logger.error(f"Failed to initialize monitoring service: {e}")
            raise HTTPException(status_code=500, detail=f"Service initialization failed: {e}")
    return _monitor_service


@app.on_event("startup")
async def startup_event():
    """Initialize application on startup"""
    logger.info("Starting AI Safety Metadata Monitor API...")
    # Pre-initialize the service
    get_monitor_service()


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    global _executor
    if _executor:
        _executor.shutdown(wait=False)
    logger.info("AI Safety Metadata Monitor API stopped")


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "status": "running",
        "service": "AI Safety Metadata Monitor",
        "version": "2.0.0",
        "timestamp": datetime.now().isoformat()
    }


@app.get("/health")
async def health_check(service: MonitoringService = Depends(get_monitor_service)):
    """Health check endpoint"""
    status = service.get_status()
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "sheets_connected": status['sheets_connected'],
        "total_urls": status['total_monitored_urls'],
        "central_check_interval": service.config.central_check_interval
    }


@app.post("/check-now")
async def manual_check(background_tasks: BackgroundTasks, service: MonitoringService = Depends(get_monitor_service)):
    """Trigger a manual monitoring cycle"""
    try:
        # Run in background to avoid blocking
        def run_cycle():
            return service.run_cycle()
        
        # Use thread pool to run blocking operation
        loop = asyncio.get_event_loop()
        stats = await loop.run_in_executor(_executor, run_cycle)
        
        return {
            "status": "completed",
            "cycle_id": stats.cycle_id,
            "changes_detected": stats.changes_detected,
            "duration_seconds": stats.duration_seconds,
            "timestamp": datetime.now().isoformat()
        }
        
    except (RuntimeError, OSError, ValueError, TypeError) as e:
        logger.error(f"Manual check failed: {e}")
        raise HTTPException(status_code=500, detail=f"Manual check failed: {e}")


@app.get("/status")
async def status(service: MonitoringService = Depends(get_monitor_service)):
    """Get current monitoring status"""
    try:
        status_info = service.get_status()
        return {
            "status": "operational",
            "timestamp": datetime.now().isoformat(),
            **status_info,
            "central_check_interval": service.config.central_check_interval
        }
    except (AttributeError, TypeError, KeyError, ValueError) as e:
        logger.error(f"Status check failed: {e}")
        raise HTTPException(status_code=500, detail=f"Status check failed: {e}")


@app.get("/api/sheets-status")
async def sheets_status(service: MonitoringService = Depends(get_monitor_service)):
    """Check Google Sheets integration status"""
    status_info = service.get_status()
    return {
        "sheets_connected": status_info['sheets_connected'],
        "last_checked": datetime.now().isoformat()
    }


@app.get("/api/urls")
async def list_urls(service: MonitoringService = Depends(get_monitor_service)):
    """List all monitored URLs"""
    try:
        urls = []
        for url_config in service.config.url_configs:
            urls.append({
                "url": url_config.url,
                "type": url_config.type,
                "priority": url_config.priority
            })
        
        return {
            "urls": urls,
            "total": len(urls),
            "central_check_interval": service.config.central_check_interval,
            "timestamp": datetime.now().isoformat()
        }
    except (AttributeError, TypeError, KeyError, ValueError) as e:
        logger.error(f"Failed to list URLs: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list URLs: {e}")


@app.get("/api/config")
async def get_config(service: MonitoringService = Depends(get_monitor_service)):
    """Get current configuration"""
    try:
        return {
            "central_check_interval": service.config.central_check_interval,
            "polling_interval": service.config.scheduling.polling_interval,
            "total_monitored_urls": len(service.config.url_configs),
            "url_priorities": {
                "high": len([u for u in service.config.url_configs if u.priority == "high"]),
                "medium": len([u for u in service.config.url_configs if u.priority == "medium"]),
                "low": len([u for u in service.config.url_configs if u.priority == "low"])
            }
        }
    except (AttributeError, TypeError, KeyError, ValueError) as e:
        logger.error(f"Failed to get config: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get config: {e}")


# Security-conscious debug endpoints (should be disabled in production)
@app.get("/debug/status")
async def debug_status(service: MonitoringService = Depends(get_monitor_service)):
    """Debug endpoint - limited information for production"""
    status_info = service.get_status()
    return {
        "service_initialized": _monitor_service is not None,
        "scheduler_status": status_info['scheduler'],
        "first_run": status_info['first_run'],
        "central_check_interval": service.config.central_check_interval,
        "environment": {
            "github_actions": os.getenv('GITHUB_ACTIONS') == 'true',
            "config_file_exists": os.path.exists('config.yaml')
        }
    }

def main():
    """Main entry point for the application"""
    try:
        # One-shot mode for GitHub Actions
        if os.getenv('GITHUB_ACTIONS') == 'true':
            logger.info("GitHub Actions environment detected - running one-shot mode")
            print("Running one-shot monitoring cycle...")
            
            service = MonitoringService()
            stats = service.run_cycle()
            
            # Log central interval info
            logger.info(f"Central check interval: {service.config.central_check_interval}s")
            
            # Exit with appropriate code
            if stats.errors > 0:
                logger.warning("Monitoring completed with errors")
                exit(1)
            else:
                logger.info("Monitoring completed successfully")
                exit(0)
        
        # Continuous monitoring mode with API
        logger.info("Starting AI Safety Metadata Monitor in continuous mode...")
        logger.info(f"Central check interval: {get_monitor_service().config.central_check_interval}s")
        
        # Start FastAPI server
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=8000,
            log_level="info",
            access_log=True
        )
        
    except (RuntimeError, OSError) as e:
        logger.error(f"Application failed to start: {e}")
        if os.getenv('GITHUB_ACTIONS') == 'true':
            exit(1)
        else:
            raise


if __name__ == "__main__":
    main()