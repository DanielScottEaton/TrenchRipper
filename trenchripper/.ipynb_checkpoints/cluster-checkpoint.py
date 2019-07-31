import trenchripper as tr
import os
import shutil
import dask
import time

from dask.distributed import Client,progress
from dask_jobqueue import SLURMCluster
from IPython.core.display import display, HTML

class dask_controller: #adapted from Charles' code
    def __init__(self,n_workers=6,local=True,queue="short",\
                 walltime='01:30:00',cores=1,processes=1,memory='6GB',job_extra=[]):
        self.local = local
        self.n_workers = n_workers
        self.walltime = walltime
        self.queue = queue
        self.processes = processes
        self.memory = memory
        self.cores = cores
        self.job_extra = job_extra
        
    def writedir(self,directory):
        if not os.path.exists(directory):
            os.makedirs(directory)
            
    def startdask(self):
        if self.local:
            self.daskclient = Client()
            self.daskclient.cluster.scale(self.n_workers)
        else:
            self.daskcluster = SLURMCluster(queue=self.queue,walltime=self.walltime,\
                                   processes=self.processes,memory=self.memory,
                                  cores=self.cores,job_extra=self.job_extra)
            self.workers = self.daskcluster.start_workers(self.n_workers)
            self.daskclient = Client(self.daskcluster)
    
    def shutdown(self):
        self.daskcluster.stop_all_jobs()
        for item in os.listdir("./"):
            if "worker-" in item or "slurm-" in item or ".lock" in item:
                path = "./" + item
                if os.path.isfile(path):
                    os.remove(path)
                elif os.path.isdir(path):
                    shutil.rmtree(path)
            
    def printprogress(self):
        complete = len([item for item in self.futures if item.status=="finished"])
        print(str(complete) + "/" + str(len(self.futures)))
        
    def displaydashboard(self):
        link = self.daskcluster.dashboard_link
        display(HTML('<a href="' + link +'">Dashboard</a>'))
        
    def mapfovs(self,function,fov_list,retries=0):
        self.function = function
        self.retries = retries
        def mapallfovs(fov_number,function=function):
            function(fov_number)
        self.futures = {}
        for fov in fov_list:
            future = self.daskclient.submit(mapallfovs,fov,retries=retries)
            self.futures[fov] = future

    def retry_failed(self):
        self.failed_fovs = [fov for fov,future in self.futures.items() if future.status != 'finished']
        self.daskclient.restart()
        time.sleep(5)
        self.mapfovs(self.function,self.failed_fovs,retries=self.retries)
        
    def retry_processing(self):
        self.proc_fovs = [fov for fov,future in self.futures.items() if future.status == 'pending']
        self.daskclient.restart()
        time.sleep(5)
        self.mapfovs(self.function,self.proc_fovs,retries=self.retries)
        
def transferjob(sourcedir,targetdir):
    mkdircmd = "mkdir -p " + targetdir
    rsynccmd = "rsync -r " + sourcedir + "/ " + targetdir
    wrapcmd = mkdircmd + " && " + rsynccmd
    cmd = "sbatch -p transfer -t 0-12:00 --wrap=\"" + wrapcmd + "\""
    os.system(cmd)