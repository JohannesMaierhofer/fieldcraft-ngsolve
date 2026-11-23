"""Summarize independently solved mesh levels and air gaps for the armature case."""
import argparse
import json
from pathlib import Path

import numpy as np


def study(manifests):
    rows = []
    runtime = None
    for manifest in manifests:
        result = json.loads(manifest.read_text())
        if runtime is not None and runtime != result['runtime']:
            raise ValueError('Use one solver runtime for a refinement study')
        runtime = result['runtime']
        q = {item['name']: item['value'] for item in result['integrals']}
        with np.load(manifest.with_name('mesh.npz'), allow_pickle=False) as mesh:
            n = len(mesh['tetrahedra'])
        level = round((n / 4608)**(1/3))
        if n != 4608*level**3 or not 1 <= level <= 4:
            raise ValueError('Unexpected mesh size for this refinement study')
        gap = round(1000*result['physics']['gap_m'], 9)
        rows.append({'gap_mm':gap, 'level':level, 'tetrahedra':n, 'dofs':result['physics']['dofs'],
                     'mesh_sha256':result['mesh_sha256'], 'model_sha256':result['model_sha256'],
                     'solver_script_sha256':result['script_sha256'],
                     'virtual_work_sha256':result['physics']['virtual_work_sha256'],
                     'force_N':q['virtual_work_force_armature'],
                     'moment_center_Nm':q['virtual_work_torque_about_armature_center'],
                     'moment_side_Nm':q['virtual_work_torque_about_side_point'],
                     'stationary_scalar_coenergy_J':q['stationary_scalar_coenergy'],
                     'relative_free_dof_residual':q['free_dof_residual']})
    rows.sort(key=lambda r:(r['gap_mm'],r['level']))
    if len({(r['gap_mm'],r['level']) for r in rows}) != len(rows):
        raise ValueError('Duplicate mesh level at one gap')
    for prev, row in zip(rows, rows[1:]):
        if row['gap_mm'] != prev['gap_mm']:
            continue
        f, old_f = np.array(row['force_N']), np.array(prev['force_N'])
        s, old_s = np.array(row['moment_side_Nm']), np.array(prev['moment_side_Nm'])
        c, old_c = np.array(row['moment_center_Nm']), np.array(prev['moment_center_Nm'])
        row['change_from_level'] = prev['level']
        row['relative_change_axial_force'] = abs((f[0]-old_f[0])/f[0])
        row['relative_change_force_vector'] = np.linalg.norm(f-old_f)/np.linalg.norm(f)
        row['relative_change_side_moment_vector'] = np.linalg.norm(s-old_s)/np.linalg.norm(s)
        row['absolute_change_center_moment_Nm'] = np.linalg.norm(c-old_c)
    return {'method':'rigid armature virtual work at fixed magnet remanence', 'frame':'global Cartesian XYZ',
            'center_m':[.003,0,0], 'side_point_m':[.003,.002,0],
            'runtime':runtime, 'rows':rows}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files', type=Path, nargs='+')
    print(json.dumps(study(parser.parse_args().files), indent=2, allow_nan=False))
