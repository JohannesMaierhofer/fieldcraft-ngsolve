"""Run an explicitly chosen project file and optionally display its result in FieldCraft."""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import time


def load_case(source):
    with source.open('rb') as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError('Case definition exceeds 64 KiB')
    case = json.loads(raw)
    allowed = {'format','model','script','dimension','materials','boundaries','interfaces','field','series','component'}
    if not isinstance(case,dict) or case.get('format') != 'fieldcraft-ngsolve-case/1' or set(case)-allowed:
        raise ValueError('Expected a fieldcraft-ngsolve-case/1 file with known keys')
    for key in ('model','script'):
        if not isinstance(case.get(key),str) or not case[key].strip():
            raise ValueError(f'Missing case {key}')
        case[key] = (source.parent/case[key]).resolve(strict=True)
    for key in ('materials','boundaries','interfaces'):
        values=case.get(key,[])
        if (not isinstance(values,list) or (not values and key!='interfaces')
                or not all(isinstance(v,str) and v.strip() for v in values) or len(set(values))!=len(values)):
            raise ValueError(f'Invalid case {key}')
        case[key]=values
    if any(k in case and (not isinstance(case[k],str) or not case[k].strip()) for k in ('field','series')) or ('field' in case and 'series' in case):
        raise ValueError('A case may select one field or one time series')
    if (case.get('dimension',3) not in (2,3) or
            case.get('component') not in (None,'x','y','z','magnitude')):
        raise ValueError('Invalid dimension or view component')
    return case


def display_new(executable,manifest,result,field,component,model,series=None):
    from show_run import Control, apply
    # Choose a loopback port for a dedicated window. The application binds it
    # after startup, so also check that the child remains alive before use.
    with socket.socket() as selector:
        selector.bind(('127.0.0.1',0))
        port=selector.getsockname()[1]
    proc=subprocess.Popen([str(executable),'--control',str(port)])
    control=Control(port)
    deadline=time.monotonic()+90
    while time.monotonic()<deadline:
        if proc.poll() is not None:
            raise RuntimeError(f'FieldCraft exited with {proc.returncode}')
        try:
            control('ui.inspect',{})
            break
        except (ConnectionRefusedError, ConnectionResetError):
            time.sleep(.2)
    else:
        raise TimeoutError('FieldCraft did not open its control channel within 90 s')
    apply(control,manifest,result,field,component,bool(result['integrals']),model,series=series)
    print(f'FieldCraft control port {port}')


def main(argv):
    from workflow import run
    from show_run import Control, apply, read_run
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case',type=Path,help='Explicitly chosen case definition containing a trusted solver script')
    parser.add_argument('out',type=Path,help='A new output directory')
    parser.add_argument('--python',default=sys.executable)
    parser.add_argument('--timeout',type=int,default=3600)
    view=parser.add_mutually_exclusive_group()
    view.add_argument('--series',help='Time series to display')
    view.add_argument('--field',help='Result to display (defaults to the case field or first result)')
    group=parser.add_mutually_exclusive_group()
    group.add_argument('--gui',type=Path,help='Open results in a new window of this FieldCraft executable')
    group.add_argument('--port',type=int,help='Show results on the current mesh of this FieldCraft control port')
    args=parser.parse_args(argv)
    case=load_case(args.case.resolve(strict=True))
    if args.gui is not None and not args.gui.is_file():
        parser.error('--gui expects a FieldCraft executable')
    if args.port is not None and not 1<=args.port<=65535:
        parser.error('Invalid control port')
    args.model=case['model']
    args.script=case['script']
    args.dimension=case.get('dimension',3)
    args.adapter=Path(__file__).resolve().parent
    args.boundaries,args.materials,args.interfaces=(case[k] for k in ('boundaries','materials','interfaces'))
    run(args)
    if args.gui or args.port:
        manifest,result=read_run(args.out)
        first=result['fields'][0]['name'] if result['fields'] else None
        series = args.series or (case.get('series') if not args.field else None)
        field = None if series else args.field or case.get('field') or first
        if not series and not field and result.get('series'):
            series = result['series'][0]['name']
        component = case.get('component') if ((field and field == case.get('field')) or
                                              (series and series == case.get('series'))) else None
        if args.gui:
            display_new(args.gui.resolve(),manifest,result,field,component,case['model'],series=series)
        else:
            apply(Control(args.port),manifest,result,field,component,bool(result['integrals']),None,series=series)
