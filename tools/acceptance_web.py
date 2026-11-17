"""Own loopback web process, stopped by its instance-local marker, never by process name."""
import argparse
import threading
import time
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import uvicorn

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--stop',type=Path,required=True);p.add_argument('--port',type=int,required=True);a=p.parse_args()
    server=uvicorn.Server(uvicorn.Config('app.main:app',host='127.0.0.1',port=a.port,log_level='warning'))
    def watch():
        while not a.stop.exists() and not server.should_exit:time.sleep(.25)
        server.should_exit=True
    threading.Thread(target=watch,daemon=True).start();server.run()
