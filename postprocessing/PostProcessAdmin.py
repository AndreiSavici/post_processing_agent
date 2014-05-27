#!/usr/bin/env python
"""
    Post-processing tasks
    
    The original code for this class was take from https://github.com/mantidproject/autoreduce
    
    Example input dictionaries:
    {"information": "mac83808.sns.gov", "run_number": "30892", "instrument": "EQSANS", "ipts": "IPTS-10674", "facility": "SNS", "data_file": "/Volumes/RAID/SNS/EQSANS/IPTS-10674/0/30892/NeXus/EQSANS_30892_event.nxs"}
    {"information": "autoreducer1.sns.gov", "run_number": "85738", "instrument": "CNCS", "ipts": "IPTS-10546", "facility": "SNS", "data_file": "/SNS/CNCS/IPTS-10546/0/85738/NeXus/CNCS_85738_event.nxs"}
    
    @copyright: 2014 Oak Ridge National Laboratory
"""
import logging, json, socket, os, sys, subprocess, time, glob, requests
import re
import string
from Configuration import configuration
from Configuration import StreamToLogger
from ingest_nexus import IngestNexus
from ingest_reduced import IngestReduced
from stompest.config import StompConfig
from stompest.sync import Stomp

class PostProcessAdmin:
    def __init__(self, data, conf):
        logging.info("json data: %s [%s]" % (str(data), type(data)))
        if not type(data) == dict:
            raise ValueError, "PostProcessAdmin expects a data dictionary"
        data["information"] = socket.gethostname()
        self.data = data
        self.conf = conf
        self.sw_dir = conf.sw_dir

        stompConfig = StompConfig(self.conf.brokers, self.conf.amq_user, self.conf.amq_pwd)
        self.client = Stomp(stompConfig)
        
        if data.has_key('data_file'):
            self.data_file = str(data['data_file'])
            logging.info("data_file: " + self.data_file)
            if os.access(self.data_file, os.R_OK) == False:
                raise ValueError("Data file does not exist or is not readable")
        else:
            raise ValueError("data_file is missing")

        if data.has_key('facility'):
            self.facility = str(data['facility']).upper()
            logging.info("facility: " + self.facility)
        else: 
            raise ValueError("Facility is missing")

        if data.has_key('instrument'):
            self.instrument = str(data['instrument']).upper()
            logging.info("instrument: " + self.instrument)
        else:
            raise ValueError("Instrument is missing")

        if data.has_key('ipts'):
            self.proposal = str(data['ipts']).upper()
            logging.info("proposal: " + self.proposal)
        else:
            raise ValueError("IPTS is missing")
            
        if data.has_key('run_number'):
            self.run_number = str(data['run_number'])
            logging.info("run_number: " + self.run_number)
        else:
            raise ValueError("Run number is missing")

    def reduce(self, remote=False):
        """
            Reduction process using job submission.
            @param remote: If True, the job will be submitted to a compute node
        """
        try:
            self.send('/queue/'+self.conf.reduction_started, json.dumps(self.data))
            instrument_shared_dir = os.path.join('/', self.facility, self.instrument, 'shared', 'autoreduce')
            proposal_shared_dir = os.path.join('/', self.facility, self.instrument, self.proposal, 'shared', 'autoreduce')

            # Allow for an alternate output directory, if defined
            if configuration.dev_output_dir is not None:
                proposal_shared_dir = configuration.dev_output_dir
            
            # Look for run summary script
            summary_script = os.path.join(instrument_shared_dir, "sumRun_%s.py" % self.instrument)
            if os.path.exists(summary_script) == True:
                summary_output = os.path.join(proposal_shared_dir, "%s_%s_runsummary.csv" % (self.instrument, self.proposal))
                cmd = "python " + summary_script + " " + self.instrument + " " + self.data_file + " " + summary_output
                logging.debug("Run summary subprocess started: " + cmd)
                subprocess.call(cmd, shell=True)
                logging.info("Run summary subprocess completed, see " + summary_output)

            # Look for auto-reduction script
            reduce_script_path = os.path.join(instrument_shared_dir, "reduce_%s.py" % self.instrument)
            if os.path.exists(reduce_script_path) == False:
                self.send('/queue/' + self.conf.reduction_disabled, json.dumps(self.data))
                return
            
            log_dir = os.path.join(proposal_shared_dir, "reduction_log")
            monitor_user = {'username': self.conf.amq_user, 'password': self.conf.amq_pwd}
            
            if not os.path.exists(log_dir):
                os.makedirs(log_dir)

            # Run the reduction
            out_log = os.path.join(log_dir, os.path.basename(self.data_file) + ".log")
            out_err = os.path.join(log_dir, os.path.basename(self.data_file) + ".err")
            if remote:
                self.remote_reduction(reduce_script_path, proposal_shared_dir, out_log, out_err)
            else:
                self.local_reduction(reduce_script_path, proposal_shared_dir, out_log, out_err)
                
            # If the reduction succeeded, upload the images we might find in the reduction directory
            if not os.path.isfile(out_err) or os.stat(out_err).st_size == 0:
                if os.path.isfile(out_err):
                    os.remove(out_err)
                self.send('/queue/'+self.conf.reduction_complete , json.dumps(self.data))
                
                url_template = string.Template(configuration.web_monitor_url)
                url = url_template.substitute(instrument=self.instrument, run_number=self.run_number)

                pattern=self.instrument+"_"+self.run_number+"*"
                for dirpath, dirnames, filenames in os.walk(proposal_shared_dir):
                    listing = glob.glob(os.path.join(dirpath, pattern))
                    for filepath in listing:
                        f, e = os.path.splitext(filepath)
                        if e.startswith(os.extsep):
                            e = e[len(os.extsep):]
                            if e == "png" or e == "jpg":
                                logging.info("filepath=" + filepath)
                                files={'file': open(filepath, 'rb')}
                                #TODO: Max image size should be properly configured
                                if len(files) != 0 and os.path.getsize(filepath) < 500000:
                                    request=requests.post(url, data=monitor_user, files=files, verify=False)
                                    logging.info("Submitted reduced image file, https post status:" + str(request.status_code))
            else:
                # Go through each line and report the error message.
                # If we can't fine the actual error, report the last line
                last_line = None
                error_line = None
                fp=file(out_err, "r")
                for l in fp.readlines():
                    if len(l.replace('-','').strip())>0:
                        last_line = l.strip()
                    result = re.search('Error: ([\w ]+)$',l)
                    if result is not None:
                        error_line = result.group(1)
                if error_line is None:
                    error_line = last_line
                    
                self.data["error"] = "REDUCTION: %s" % error_line
                self.send('/queue/'+self.conf.reduction_error , json.dumps(self.data))
        except:
            logging.error("reduce: %s" % sys.exc_value)
            self.data["error"] = "Reduction: %s " % sys.exc_value
            self.send('/queue/'+self.conf.reduction_error , json.dumps(self.data))

    def remote_reduction(self, script, output_dir, out_log, out_err):
        """
            Run auto-reduction remotely
            @param script: full path to the reduction script to run
            @param output_dir: reduction output directory
            @param out_log: reduction log file
            @param out_err: reduction error file
        """
        #MaxChunkSize is set to 8G specifically for the jobs run on fermi, which has 32 nodes and 64GB/node
        #We would like to get MaxChunkSize from an env variable in the future
        if self.conf.comm_only is False:
            import mantid.simpleapi as api
            chunks = api.DetermineChunking(Filename=self.data_file,MaxChunkSize=configuration.max_memory)
            nodes_desired = min(chunks.rowCount(), configuration.max_nodes)
        else:
            chunks = 1
            nodes_desired = 1
        logging.info("Chunks: " + str(chunks))
        logging.info("nodesDesired: " + str(nodes_desired))
        
        # Build qsub command
        #TODO: Pass in the reduction script path directly instead of rebuilding it inside the job script.
        cmd_out = " -o " + out_log + " -e " + out_err
        cmd_l = " -l nodes=" + str(nodes_desired) + ":ppn=1"
        cmd_v = " -v data_file='" + self.data_file + "',n_nodes="+str(nodes_desired)+",facility='" + self.facility + "',instrument='" + self.instrument + "',proposal_shared_dir='" + output_dir + "'"
        cmd_job = " " + self.sw_dir + "/remoteJob.sh"
        cmd = "qsub" + cmd_out + cmd_l + cmd_v + cmd_job
        logging.info("Reduction process: " + cmd)

        # If we are only dry-running, return immediately
        if self.conf.comm_only is True:
            return
        
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, shell=True).stdout.read()
        list = proc.split(".")
        if len(list) > 0:
            pid = list[0].rstrip()

        qstat_pid = "qstat: Unknown Job Id " + pid
        logging.debug("qstat_pid: " + qstat_pid)
        
        while True:
            qstat_cmd = "qstat " + pid
            ret = subprocess.Popen(qstat_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True).stdout.read().rstrip()
            logging.debug("Popen return code: " + ret)
            if ret.startswith(qstat_pid):
                break
            else:
                time.sleep(30)
    
    def local_reduction(self, script, output_dir, out_log, out_err):
        """
            Run auto-reduction locally
            @param script: full path to the reduction script to run
            @param output_dir: reduction output directory
            @param out_log: reduction log file
            @param out_err: reduction error file
        """
        cmd = "python " + script + " " + self.data_file + " " + output_dir
        logFile=open(out_log, "w")
        errFile=open(out_err, "w")
        if self.conf.comm_only is False:
            proc = subprocess.Popen(cmd, shell=True, stdin=subprocess.PIPE,
                                    stdout=logFile, stderr=errFile, universal_newlines = True)
            proc.communicate()
        logFile.close()
        errFile.close()

    def catalog_raw(self):
        """
            Catalog a nexus file containing raw data
        """
        try:
            self.send('/queue/'+self.conf.catalog_started, json.dumps(self.data))
            if self.conf.comm_only is False:
                ingestNexus = IngestNexus(self.data_file)
                ingestNexus.execute()
                ingestNexus.logout()
                self.send('/queue/'+self.conf.catalog_complete, json.dumps(self.data))  
        except:
            logging.error("catalog_raw: %s" % sys.exc_value)
            self.data["error"] = "Catalog: %s" % sys.exc_value
            self.send('/queue/'+self.conf.catalog_error, json.dumps(self.data))
            
    def catalog_reduced(self):
        """
            Catalog reduced data files for a given run
        """
        try:
            self.send('/queue/'+self.conf.reduction_catalog_started, json.dumps(self.data))
            if self.conf.comm_only is False:
                ingestReduced = IngestReduced(self.facility, self.instrument, self.proposal, self.run_number)
                ingestReduced.execute()
                ingestReduced.logout()
            self.send('/queue/'+self.conf.reduction_catalog_complete , json.dumps(self.data))
        except:
            logging.error("catalog_reduced: %s" % sys.exc_value)
            self.data["error"] = "Reduction catalog: %s" % sys.exc_value
            self.send('/queue/'+self.conf.reduction_catalog_error , json.dumps(self.data))
            
    def send(self, destination, data):
        """
            Send an AMQ message
            @param destination: AMQ queue to send to
            @param data: payload of the message
        """
        logging.info("%s: %s" % (destination, data))
        self.client.connect()
        self.client.send(destination, data)
        self.client.disconnect()
    
if __name__ == "__main__":
    import argparse
    from Configuration import read_configuration
    parser = argparse.ArgumentParser(description='Post-processing agent')
    parser.add_argument('-q', metavar='queue', help='ActiveMQ queue name', dest='queue', required=True)
    parser.add_argument('-c', metavar='config', help='Configuration file', dest='config')
    parser.add_argument('-d', metavar='data', help='JSON data', dest='data')
    parser.add_argument('-f', metavar='data_file', help='Nexus data file', dest='data_file')
    namespace = parser.parse_args()
    
    try:
        # Refresh configuration is we need to use an alternate configuration
        if namespace.config is not None:
            configuration = read_configuration(namespace.config)
    
        # If we have no data dictionary, try to create one
        if namespace.data is None:
            if namespace.data_file is not None:
                data = {"facility": "SNS", "data_file": namespace.data_file}
                file_name = os.path.basename(namespace.data_file)
                toks = file_name.split('_')
                if len(toks)>1:
                    data["instrument"] = toks[0].upper()
                    try:
                        data["run_number"] = str(int(toks[1]))
                    except:
                        logging.error("Could not determine run number")
                    ipts_toks = namespace.data_file.upper().split(toks[0].upper())
                    if len(ipts_toks)>1:
                        sep_toks = ipts_toks[1].split('/')
                        if len(sep_toks)>1:
                            data["ipts"] = sep_toks[1]
                logging.info("Reconstructed dict: %s" % str(data))
            else:
                raise RuntimeError, "Expected a JSON object or a file path"
        else:
            data = json.loads(namespace.data)
            
        # Process the data
        try:
            pp = PostProcessAdmin(data, configuration)
            logging.info("Processing: %s" % namespace.queue)
            if namespace.queue == '/queue/%s' % configuration.reduction_data_ready:
                pp.reduce(configuration.remote_execution)
            elif namespace.queue == '/queue/%s' % configuration.catalog_data_ready:
                pp.catalog_raw()
            elif namespace.queue == '/queue/%s' % configuration.reduction_catalog_data_ready:
                pp.catalog_reduced()
        except:
            # If we have a proper data dictionary, send it back with an error message
            if type(data) == dict:
                data["error"] = str(sys.exc_value)
                stomp = Stomp(StompConfig(configuration.brokers, configuration.amq_user, configuration.amq_pwd))
                stomp.connect()
                stomp.send(configuration.postprocess_error, json.dumps(data))
                stomp.disconnect()
            raise
    except:
        logging.error("PostProcessAdmin: %s" % sys.exc_value)