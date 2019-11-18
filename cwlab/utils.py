import os, sys
from re import sub, match
from datetime import datetime
from time import sleep
from . import app
from cwlab.wf_input.web_interface import read_template_attributes as read_template_attributes_from_xls
from cwlab.wf_input.web_interface import get_param_config_info as get_param_config_info_from_xls
from cwlab.wf_input import generate_xls_from_cwl as generate_job_template_from_cwl
from cwlab import db
from random import random, choice as random_choice
from pathlib import Path
import zipfile
from cwltool.load_tool import fetch_document
from cwltool.main import print_pack
import json
from string import ascii_letters, digits
from pkg_resources import get_distribution
from urllib import request as url_request
from shutil import copyfileobj
from werkzeug import secure_filename
from urllib.request import urlopen
cwltool_version = get_distribution("cwltool").version
from distutils.version import StrictVersion
if StrictVersion(cwltool_version) > StrictVersion("1.0.20181201184214"):
    from cwltool.load_tool import resolve_and_validate_document
else:
    from cwltool.load_tool import validate_document
basedir = os.path.abspath(os.path.dirname(__file__))

def get_time_string():
    return datetime.now().strftime("%H:%M:%S")

def normalize_path(path):
    if app.config["CORRECT_SYMLINKS"]:
        return os.path.realpath(path)
    else:
        return os.path.abspath(path)

def vaidate_url(url):
    try:
        test = urlopen(url)
    except Exception:
        raise AssertionError("Cannot open the provided url: {}".format(url))
        
def browse_dir(path,
    ignore_files=False,
    file_exts=[],
    show_only_hits=False
    ):
    file_exts = ["."+e for e in file_exts]
    abs_path = os.path.abspath(path)
    try:
        dir_content_ = list(Path(abs_path).iterdir())
    except Exception as e:
        raise AssertionError("Path does not exist or you have no permission to enter it.")
    dir_content_dict = {}
    for item in dir_content_:
        is_dir = item.is_dir()
        if is_dir or not ignore_files:
            abs_path = str(item.absolute())
            name = os.path.basename(abs_path)
            file_ext = None if is_dir else os.path.splitext(abs_path)[1]
            hit = True if not is_dir and (len(file_exts) == 0 or file_ext in file_exts) else False
            if not show_only_hits or hit:
                dir_content_dict[name] = {
                    "name": name,
                    "abs_path": abs_path,
                    "is_dir": is_dir,
                    "file_ext": file_ext,
                    "hit": hit
                }
    dir_content = [dir_content_dict[name] for name in sorted(dir_content_dict.keys())]
    return(dir_content)

def fetch_files_in_dir(dir_path, # searches for files in dir_path
    file_exts, # match files with extensions in this list
    search_string="", # match files that contain this string in the name
                        # "" to disable
    regex_pattern="", # matches files by regex pattern
    ignore_subdirs=True # if true, ignores subdirectories
    ):
    # searches for files in dir_path
    # onyl hit that fullfill following criteria are return:
    #   - file extension matches one entry in the file_exts list
    #   - search_string is contained in the file name ("" to disable)
    file_exts = ["."+e for e in file_exts]
    hits = []
    abs_dir_path = os.path.abspath(dir_path)
    for root, dir_, files in os.walk(abs_dir_path):
        for file_ in files:
            file_ext = os.path.splitext(file_)[1]
            if file_ext not in file_exts:
                continue
            if search_string != "" and search_string not in file_:
                continue
            if search_string != "" and not match(regex_pattern, file_):
                continue
            if ignore_subdirs and os.path.abspath(root) != abs_dir_path:
                continue
            file_reldir = os.path.relpath(root, abs_dir_path)
            file_relpath = os.path.join(file_reldir, file_) 
            file_nameroot = os.path.splitext(file_)[0]
            hits.append({
                "file_name":file_, 
                "file_nameroot":file_nameroot, 
                "file_relpath":file_relpath, 
                "file_reldir":file_reldir, 
                "file_ext":file_ext
            })
    return hits


def read_file_content(
    file_path,
    start_pos=0, # anticipated starting point
    max_chars=app.config["READ_MAX_CHARS_FROM_FILE"] # maximum number of charcters to read in
):
    content = []
    fsize = os.stat(file_path).st_size
    if fsize > max_chars:
        start_pos = max(fsize-max_chars, start_pos)
    with open(file_path, 'r') as f:
        f.seek(start_pos)
        content = f.read()
        end_pos = f.tell()
    return str(content), end_pos

allowed_extensions_by_type = {
    "CWL": ["cwl", "yaml", "yml", "CWL"],
    "spreadsheet": ["xlsx", "ods", "xls"],
    "zip": ["zip"],
    "janis": "py"
}

def zip_dir(dir_path):
    zip_path = dir_path + ".cwlab.zip"
    contents = os.walk(dir_path)
    zip_file = zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED)
    for root, dirs, files in contents:
        for dir_ in dirs:
            absolute_path = os.path.join(root, dir_)
            relative_path = absolute_path.replace(dir_path + '\\', '')
            zip_file.write(absolute_path, relative_path)
        for file_ in files:
            if file_.endswith(".cwlab.zip"):
                continue
            absolute_path = os.path.join(root, file_)
            relative_path = absolute_path.replace(dir_path + '\\', '')
            zip_file.write(absolute_path, relative_path)
    zip_file.close()
    return(zip_path)
    
def unzip_dir(zip_path, target_dir):
    zip_path=os.path.abspath(zip_path)
    assert zipfile.is_zipfile(zip_path), "The provided file is not a zip."
    assert os.path.isdir(target_dir), "The provided target dir does not exist or is not a dir."
    with zipfile.ZipFile(zip_path,"r") as zip_ref:
        zip_ref.extractall(target_dir)

def download_file(url, fallback_filename=None):
    temp_dir = make_temp_dir()
    try:
        file_name = secure_filename(url.rsplit('/', 1)[-1])
    except Exception:
        
        file_name = fallback_filename if not fallback_filename is None else "download"
    file_path = os.path.join(temp_dir, file_name)
    with url_request.urlopen(url) as url_response, open(file_path, 'wb') as download_file:
        copyfileobj(url_response, download_file)

    return file_path

def is_allowed_file(filename, type="CWL"):
    # validates uploaded files
    return '.' in filename and \
           os.path.splitext(filename)[1].strip(".").lower() in allowed_extensions_by_type[type]

def get_duration(start_time, end_time):
    if not end_time:
        end_time=datetime.now()
    delta = end_time - start_time
    days = delta.days
    hours = delta.seconds//3600
    minutes = (delta.seconds//60)%60
    return [days, hours, minutes]

def get_job_ids():
    exec_dir = app.config["EXEC_DIR"]
    job_ids = [d for d in os.listdir(exec_dir) if os.path.isdir(os.path.join(exec_dir, d))]
    return job_ids

def get_job_name_from_job_id(job_id):
    return match('(\d+)_(\d+)_(.+)', job_id).group(3)

def get_path(which, job_id=None, run_id=None, param_sheet_format=None, cwl_target=None):
    if which == "job_dir":
        path = os.path.join(app.config["EXEC_DIR"], job_id)
    elif which == "runs_out_dir":
        path = os.path.join(app.config["EXEC_DIR"], job_id, "runs_out")
    elif which == "run_out_dir":
        path = os.path.join(app.config["EXEC_DIR"], job_id, "runs_out", run_id)
    elif which == "job_param_sheet":
        if param_sheet_format:
            path = os.path.join(app.config["EXEC_DIR"], job_id, "param_sheet." + param_sheet_format)
        else:
            path = os.path.join(app.config["EXEC_DIR"], job_id)
            hits = fetch_files_in_dir(path, allowed_extensions_by_type["spreadsheet"], "param_sheet")
            assert len(hits) != 0, "No spreadsheet found for job " + job_id
            path = os.path.join(path, hits[0]["file_name"])
    elif which == "job_cwl":
        path = os.path.join(app.config["EXEC_DIR"], job_id, "main.cwl")
    elif which == "job_param_sheet_temp":
        if param_sheet_format:
            path = os.path.join(app.config["EXEC_DIR"], job_id, "job_templ." + param_sheet_format)
        else:
            path = os.path.join(app.config["EXEC_DIR"], job_id)
            hits = fetch_files_in_dir(path, allowed_extensions_by_type["spreadsheet"], "job_templ")
            assert len(hits) != 0, "No spreadsheet found for job " + job_id
            path = os.path.join(path, hits[0]["file_name"])
    elif which == "runs_yaml_dir":
        path = os.path.join(app.config["EXEC_DIR"], job_id, "runs_params")
    elif which == "run_yaml":
        path = os.path.join(app.config["EXEC_DIR"], job_id, "runs_params", run_id + ".yaml")
    elif which == "job_templ":
        path = os.path.join(app.config['CWL_DIR'], cwl_target + ".job_templ.xlsx")
    elif which == "cwl":
        path = os.path.join(app.config['CWL_DIR'], cwl_target)
    elif which == "runs_log_dir":
        path = os.path.join(app.config['EXEC_DIR'], job_id, "runs_log")
    elif which == "run_log":
        path = os.path.join(app.config['EXEC_DIR'], job_id, "runs_log", run_id + ".log")
    elif which == "debug_run_log":
        path = os.path.join(app.config['EXEC_DIR'], job_id, "runs_log", run_id + ".debug.log")
    elif which == "runs_input_dir":
        path = os.path.join(app.config['EXEC_DIR'], job_id, "runs_inputs")
    elif which == "error_log":
        path = os.path.join(app.config['LOG_DIR'], "error.log")
    elif which == "info_log":
        path = os.path.join(app.config['LOG_DIR'], "info.log")
    return normalize_path(path)

def make_temp_dir():
    for try_ in range(0,10):
        random_string = "".join([random_choice(ascii_letters + digits) for c in range(0,14)])
        temp_dir = os.path.join(app.config["TEMP_DIR"], random_string)
        if not os.path.exists(temp_dir):
            break
    try:
        os.mkdir(temp_dir)
    except Exception as e:
        raise AssertionError("Could not create temporary directory.")
    return temp_dir

def pack_cwl(cwl_path):
    if StrictVersion(cwltool_version) > StrictVersion("1.0.20181201184214"):
        loadingContext, workflowobj, uri = fetch_document(cwl_path)
        loadingContext.do_update = False
        loadingContext, uri = resolve_and_validate_document(loadingContext, workflowobj, uri)
        processobj = loadingContext.loader.resolve_ref(uri)[0]
        packed_cwl = json.loads(print_pack(loadingContext.loader, processobj, uri, loadingContext.metadata))
    else:
        document_loader, workflowobj, uri = fetch_document(cwl_path)
        document_loader, _, processobj, metadata, uri = validate_document(document_loader, workflowobj, uri, [], {})
        packed_cwl = json.loads(print_pack(document_loader, processobj, uri, metadata))
    return packed_cwl

def import_cwl(cwl_path, name=None):
    if name is None:
        name = os.path.splitext(os.path.basename(cwl_path))[0]
    if os.path.splitext(name)[1] in allowed_extensions_by_type["CWL"]:
        name = os.path.splitext(name)[0]
    cwl_target_name = name + ".cwl"
    packed_cwl = pack_cwl(cwl_path)
    cwl_target_path = get_path("cwl", cwl_target=cwl_target_name)
    if os.path.exists(cwl_target_path):
        try:
            os.remove(cwl_target_path)
        except Exception as e:
            raise AssertionError("Could not remove existing cwl file.")
    try:
        with open(cwl_target_path, 'w') as cwl_file:
            json.dump(packed_cwl, cwl_file)
    except Exception as e:
        raise AssertionError("Could not write CWL file.")
    job_templ_filepath = get_path("job_templ", cwl_target=cwl_target_name)
    generate_job_template_from_cwl(
        cwl_file=cwl_target_path, 
        output_file=job_templ_filepath, 
        show_please_fill=True
    )
    
def import_janis(janis_script_path, name=None, name_in_janis_script=None):
    if name is None:
        name = name_in_janis_script if not name_in_janis_script is None \
            else os.path.splitext(os.path.basename(janis_script_path))[0]
    if os.path.splitext(name)[1] in allowed_extensions_by_type["janis"]:
        name = os.path.splitext(name)[0]
    
def get_run_ids(job_id):
    exec_dir = app.config["EXEC_DIR"]
    runs_yaml_dir = get_path("runs_yaml_dir", job_id)
    run_yamls = fetch_files_in_dir(
        dir_path=runs_yaml_dir, 
        file_exts=["yaml"],
        ignore_subdirs=True
    )
    run_ids = [r["file_nameroot"] for r in run_yamls]
    return run_ids

def get_job_templates():
    # read list of template files:
    templates = fetch_files_in_dir(
        dir_path=app.config['CWL_DIR'], 
        file_exts=["xlsx"],
        search_string=".job_templ",
        ignore_subdirs=True
    )
    # add field for cwl_target
    for i, t  in enumerate(templates):
        templates[i]["cwl_target"] = sub(r'\.job_templ$', '', t["file_nameroot"])
    return templates

    
def get_job_templ_info(which, cwl_target=None, job_templ_filepath=None):
    if job_templ_filepath is None:
        job_templ_filepath = get_path("job_templ", cwl_target=cwl_target)
    if which =="config":
        info = get_param_config_info_from_xls(job_templ_filepath)
    elif which =="attributes":
        info = read_template_attributes_from_xls(job_templ_filepath)
    return info

def output_example_config():
    example_config_file = open(app.config["DEFAULT_CONFIG_FILE"])
    example_config_content = example_config_file.read()
    example_config_file.close()
    print("# For help, please visit: " + 
        "https://github.com/CompEpigen/CWLab#configuration")
    print(example_config_content)
    
def db_commit(retry_delays=[1,4]):
    for retry_delay in retry_delays:
        try:
            db.session.commit()
            break
        except Exception as e:
            assert retry_delay != retry_delays[-1], "Could not connect to database."
            sleep(retry_delay + retry_delay*random())
    
def get_allowed_base_dirs(job_id=None, run_id=None, allow_input=True, allow_upload=True, allow_download=False, include_tmp_dir=False):
    allowed_dirs = {}
    if allow_input and (not allow_download) and include_tmp_dir:
        allowed_dirs["OUTPUT_DIR_CURRENT_JOB"] = {
            "path": app.config["TEMP_DIR"],
            "mode": "input"
        }
    if (app.config["DOWNLOAD_ALLOWED"] and allow_download) or (allow_input and not allow_download):
        mode = "download" if (app.config["DOWNLOAD_ALLOWED"] and allow_download) else "input"
        if not job_id is None:
            allowed_dirs["OUTPUT_DIR_CURRENT_JOB"] = {
                "path": get_path("runs_out_dir", job_id=job_id),
                "mode": mode
            }
        if not run_id is None:
            allowed_dirs["OUTPUT_DIR_CURRENT_RUN"] = {
                "path": get_path("run_out_dir", job_id=job_id, run_id=run_id),
                "mode": mode
            }
    if (app.config["UPLOAD_ALLOWED"] and allow_upload) or (allow_input and not allow_download):
        mode = "upload" if app.config["UPLOAD_ALLOWED"] and allow_upload else "input"
        if not job_id is None:
            allowed_dirs["INPUT_DIR_CURRENT_JOB"] = {
                "path": get_path("runs_input_dir", job_id=job_id),
                "mode": mode
            }
        for dir_ in app.config["ADD_INPUT_UPLOAD_DIRS"].keys():
            if dir_ not in allowed_dirs.keys():
                allowed_dirs[dir_] = {
                    "path": app.config["ADD_INPUT_UPLOAD_DIRS"][dir_],
                    "mode": mode
                }
    if not allow_download and allow_input:
        if not job_id is None:
            allowed_dirs["EXEC_DIR_CURRENT_JOB"] = {
                "path": get_path("job_dir", job_id=job_id),
                "mode": "input"
            }
        allowed_dirs["EXEC_DIR_ALL_JOBS"] = {
            "path": app.config["EXEC_DIR"],
            "mode": "input"
        }
        for dir_ in app.config["ADD_INPUT_DIRS"].keys():
            if dir_ not in allowed_dirs.keys():
                allowed_dirs[dir_] = {
                    "path": app.config["ADD_INPUT_DIRS"][dir_],
                    "mode": "input"
                }
    return allowed_dirs


def check_if_path_in_dirs(path, dir_dict):
    hit = ""
    hit_key = None
    path = normalize_path(path)
    for dir_ in dir_dict.keys():
        dir_path = normalize_path(dir_dict[dir_]["path"])
        if path.startswith(dir_path) and len(hit) < len(dir_path):
            hit=dir_path
            hit_key = dir_
    return hit_key