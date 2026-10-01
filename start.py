"""Single-process server launcher for Railway and Docker."""
import os
import uvicorn


def server_options():
    port = int(os.getenv('PORT','8000'))
    if not 1 <= port <= 65535:
        raise ValueError('PORT must be between 1 and 65535.')
    return dict(host='0.0.0.0',port=port,workers=1,proxy_headers=True,
                forwarded_allow_ips=os.getenv('FORWARDED_ALLOW_IPS','127.0.0.1'))


if __name__=='__main__':
    uvicorn.run('main:app',**server_options())
