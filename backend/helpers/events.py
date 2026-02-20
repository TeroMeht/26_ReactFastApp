from collections import deque
from schemas.api_schemas import CandleRow

class SSEEvent:
    EVENTS = deque()
    
    @staticmethod
    def add_event(event: CandleRow):
        SSEEvent.EVENTS.append(event)
    
    @staticmethod
    def get_event():
        if len(SSEEvent.EVENTS)>0:
            return SSEEvent.EVENTS.popleft()
        return None
    
    @staticmethod
    def count():
        return len(SSEEvent.EVENTS)