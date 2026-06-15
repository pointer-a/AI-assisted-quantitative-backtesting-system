"""python -m exelixi.web 启动 Web 服务器。"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("exelixi.web.server:app", host="127.0.0.1", port=8000, reload=True)
