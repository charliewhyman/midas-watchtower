"""FastAPI application for AI Safety Metadata Monitor"""
import os
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
import requests
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
        except Exception as e:
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
        "changedetection_available": status['changedetection_available'],
        "total_urls": status['total_monitored_urls']
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
        
    except Exception as e:
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
            **status_info
        }
    except Exception as e:
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
                "check_interval": url_config.check_interval,
                "type": url_config.type,
                "priority": url_config.priority
            })
        
        return {
            "urls": urls,
            "total": len(urls),
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Failed to list URLs: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list URLs: {e}")


# Security-conscious debug endpoints (should be disabled in production)
@app.get("/debug/status")
async def debug_status(service: MonitoringService = Depends(get_monitor_service)):
    """Debug endpoint - limited information for production"""
    status_info = service.get_status()
    return {
        "service_initialized": _monitor_service is not None,
        "scheduler_status": status_info['scheduler'],
        "first_run": status_info['first_run'],
        "environment": {
            "github_actions": os.getenv('GITHUB_ACTIONS') == 'true',
            "config_file_exists": os.path.exists('config.yaml')
        }
    }

@app.post("/api/setup-watches")
async def setup_watches(service: MonitoringService = Depends(get_monitor_service)):
    """Trigger changedetection.io watch setup"""
    try:
        service.changedetection_service.setup_watches(service.change_detector)
        return {"status": "success", "message": "Watches setup completed"}
    except Exception as e:
        logger.error(f"Failed to setup watches: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to setup watches: {e}")

@app.get("/api/watches")
async def list_watches(service: MonitoringService = Depends(get_monitor_service)):
    """List current watches in changedetection.io"""
    try:
        watches = service.changedetection_service._get_existing_watches()
        return {
            "total_watches": len(watches),
            "watches": list(watches.keys())
        }
    except Exception as e:
        logger.error(f"Failed to list watches: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list watches: {e}")

@app.post("/api/setup-watches")
async def setup_watches(service: MonitoringService = Depends(get_monitor_service)):
    """Trigger changedetection.io watch setup manually"""
    try:
        service._setup_changedetection_with_retry()
        return {"status": "success", "message": "Watch setup completed"}
    except Exception as e:
        logger.error(f"Failed to setup watches: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to setup watches: {e}")

@app.get("/api/watches")
async def list_watches():
    """List current watches in changedetection.io"""
    try:
        response = requests.get("http://changedetection:5000/api/v1/watch", timeout=10)
        if response.status_code == 200:
            watches = response.json()
            return {"total_watches": len(watches), "watches": watches}
        else:
            return {"error": f"API returned {response.status_code}"}
    except Exception as e:
        return {"error": str(e)}
    
def main():
    """Main entry point for the application"""
    try:
        # One-shot mode for GitHub Actions
        if os.getenv('GITHUB_ACTIONS') == 'true':
            logger.info("GitHub Actions environment detected - running one-shot mode")
            print("Running one-shot monitoring cycle...")
            
            service = MonitoringService()
            stats = service.run_cycle()
            
            # Exit with appropriate code
            if stats.errors > 0:
                logger.warning("Monitoring completed with errors")
                exit(1)
            else:
                logger.info("Monitoring completed successfully")
                exit(0)
        
        # Continuous monitoring mode with API
        logger.info("Starting AI Safety Metadata Monitor in continuous mode...")
        
        # Start FastAPI server
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=8000,
            log_level="info",
            access_log=True
        )
        
    except Exception as e:
        logger.error(f"Application failed to start: {e}")
        if os.getenv('GITHUB_ACTIONS') == 'true':
            exit(1)
        else:
            raise


if __name__ == "__main__":
    main()