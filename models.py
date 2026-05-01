from pydantic import BaseModel
from typing import Optional, List

class ClientRequest(BaseModel):
    cmd: str = "request.get"
    cookies: Optional[List[dict]] = None
    maxTimeout: int = 60000
    url: Optional[str] = None
    postData: Optional[str] = None
    clear_session: bool = False
    proxy: Optional[str] = None   # Exemplo: "socks5://127.0.0.1:1080"

class Solution(BaseModel):
    url: Optional[str] = None
    status: Optional[int] = None
    response: Optional[str] = None
    cookies: Optional[List[dict]] = None
    userAgent: Optional[str] = None
    turnstile_token: Optional[str] = None

class ClientResponse(BaseModel):
    status: str = "ok"
    message: str = ""
    version: str = "2.0.0"
    solution: Optional[Solution] = None