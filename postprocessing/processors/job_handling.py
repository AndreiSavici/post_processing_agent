"""
    Handling of job execution.

    @copyright: 2014 Oak Ridge National Laboratory
"""
import logging
import subprocess
import time
import os
import re

def remote_submission(configuration, script, input_file, 
                      output_dir, out_log, out_err, wait=True,
                      dependencies=[], node_request=None):
    """
        Run a script remotely
        @param configuration: configuration object
        @param script: full path to the reduction script to run
        @param input_file: input file to pass along to the script
        @param output_dir: reduction output directory
        @param out_log: reduction log file
        @param out_err: reduction error file
        @param wait: if True, we will wait for the job to finish before returning
        @param dependencies: list of job dependencies
        @param node_request: None, or the number of nodes we want mpi to run on
    """
    # Check whether we requested a specific number of nodes:
    if node_request is not None:
        try:
            nodes_desired = min(int(node_request), configuration.max_nodes)
        except:
            nodes_desired = 1
            logging.error("Could not process node request: %s" % node_request)
    #MaxChunkSize is set to 8G specifically for the jobs run on fermi, which has 32 nodes and 64GB/node
    #We would like to get MaxChunkSize from an env variable in the future
    elif configuration.comm_only is False:
        import mantid.simpleapi as api
        chunks = api.DetermineChunking(Filename=input_file,
                                       MaxChunkSize=configuration.max_memory)
        nodes_desired = min(chunks.rowCount(), configuration.max_nodes)
        if nodes_desired == 0:
            nodes_desired = 1
    else:
        nodes_desired = 1
    logging.debug("Nodes: %s" % nodes_desired)

    # Build qsub command
    cmd_out = " -o %s -e %s" % (out_log, out_err)
    cmd_l = " -l nodes=%s:ppn=%s" % (nodes_desired, configuration.processors_per_node)
    cmd_v = " -v data_file='%s',n_nodes=%s,reduce_script='%s',proposal_shared_dir='%s/'" % (input_file, nodes_desired, script, output_dir)
    cmd = "qsub %s %s %s %s" % (cmd_out, cmd_l, cmd_v, configuration.remote_script)
    if len(dependencies)>0:
        cmd += " -W depend=afterok:%s" % ':'.join(dependencies)
    logging.info("Reduction process: " + cmd)

    # If we are only dry-running, return immediately
    if configuration.comm_only is True:
        return

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)

    # Catch errors in the job submission and raise them as exception
    proc_err = proc.stderr.read()
    if len(proc_err)>0:
        raise RuntimeError, proc_err

    # Read in the job ID
    proc_out = proc.stdout.read()
    toks = proc_out.split(".")
    if len(toks) > 0:
        pid = toks[0].rstrip()
    logging.info("Job ID: %s" % pid)

    # Wait for the job to finish
    t_0 = time.time()
    t_cycle = t_0
    while wait:
        qstat_cmd = "qstat " + pid
        ret = subprocess.Popen(qstat_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True).stdout.read().rstrip()
        logging.debug("Popen return code: " + ret)
        if ret.startswith("qstat: Unknown Job Id") or \
           ret.endswith("C batch") or \
           len(ret)==0:
            break
        else:
            time.sleep(30)
        # If we've been waiting for more than a configured waiting time,
        # log the event as information
        if time.time()-t_cycle > configuration.wait_notification_period:
            wait_time = time.time()-t_0
            t_cycle = time.time()
            logging.info("Waiting for job ID %s for more than %g seconds" % (pid, wait_time))
    return pid

def local_submission(configuration, script, input_file, output_dir, out_log, out_err):
    """
        Run a script locally
        @param configuration: configuration object
        @param script: full path to the reduction script to run
        @param input_file: input file to pass along to the script
        @param output_dir: reduction output directory
        @param out_log: reduction log file
        @param out_err: reduction error file
    """
    cmd = "%s %s %s %s/" % (configuration.python_executable, script, input_file, output_dir)
    logFile=open(out_log, "w")
    errFile=open(out_err, "w")
    if configuration.comm_only is False:
        proc = subprocess.Popen(cmd, shell=True, stdin=subprocess.PIPE,
                                stdout=logFile, stderr=errFile, universal_newlines = True,
                                cwd=output_dir)
        proc.communicate()
    logFile.close()
    errFile.close()

def determine_success_local(configuration, out_err):
    """
        Determine whether we generated an error
        @param configuration: configuration object
        @param out_err: job error file
    """
    success = not os.path.isfile(out_err) or os.stat(out_err).st_size == 0
    data = {}
    if not success:
        # Go through each line and report the error message.
        # If we can't fine the actual error, report the last line
        last_line = None
        error_line = None
        fp = file(out_err, "r")
        for l in fp.readlines():
            if len(l.replace('-', '').strip()) > 0:
                last_line = l.strip()
            result = re.search('Error: (.+)$', l)
            if result is not None:
                error_line = result.group(1)
        if error_line is None:
            error_line = last_line
        for item in configuration.exceptions:
            if re.search(item, error_line):
                success = True
                data["information"] = error_line
                logging.error("Reduction error ignored: %s" % error_line)

        if not success:
            data["error"] = "REDUCTION: %s" % error_line

    return success, data