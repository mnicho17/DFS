import threading
import traceback
from PyQt5 import QtCore
from performance_review import analyze_saved_results,refresh_season_stats

class PerformanceReviewWorker(QtCore.QObject):
    progress=QtCore.pyqtSignal(int,int,str)
    finished=QtCore.pyqtSignal(dict)
    error=QtCore.pyqtSignal(str)
    def __init__(self,username,season=None):
        super().__init__();self.username=username;self.season=season;self.stop=threading.Event()
    def request_cancel(self):self.stop.set()
    def run(self):
        try:
            kwargs=dict(cancelled=self.stop.is_set,progress=lambda text:self.progress.emit(0,0,text))
            result=refresh_season_stats(self.season,**kwargs) if self.season is not None else analyze_saved_results(username=self.username,**kwargs)
            self.finished.emit(result)
        except Exception:self.error.emit(traceback.format_exc())
