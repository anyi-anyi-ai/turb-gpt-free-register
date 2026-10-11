import argparse
import uvicorn

def main():
    parser = argparse.ArgumentParser(description="Start Grok Video Generation API Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to listen on (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5055, help="Port to listen on (default: 5055)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    args = parser.parse_args()

    print(f"\n=======================================================")
    print(f"[*] Grok Imagine Video API Service Starting...")
    print(f"[*] API URL:  http://localhost:{args.port}")
    print(f"[*] Swagger:  http://localhost:{args.port}/docs")
    print(f"[*] Video Endpoint: POST http://localhost:{args.port}/v1/videos/generations")
    print(f"=======================================================\n")

    uvicorn.run("core.video_api_service:app", host=args.host, port=args.port, reload=args.reload)

if __name__ == "__main__":
    main()
