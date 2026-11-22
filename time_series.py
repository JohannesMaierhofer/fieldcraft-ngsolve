"""Write and check SI-time scalar or vector fields on a single immutable mesh."""
import json
import math
from pathlib import Path

import numpy as np

FRAME_LIMIT = 4096
FIELD_BYTES = 64 * 1024 * 1024
MANIFEST_BYTES = 4 * 1024 * 1024


def sibling(directory, filename):
    if (not isinstance(filename, str) or filename in ('', '.', '..')
            or any(char in filename for char in '/\\:\x00') or not filename.isprintable()):
        raise ValueError('Expected a filename in the run directory')
    path = directory / filename
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'Missing or linked series file: {filename}')
    return path


def read_limited(path, limit):
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError(f'Series file exceeds its size limit: {path.name}')
    return json.loads(raw)


def next_time(value, previous):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= previous:
        raise ValueError('Series times must be finite and strictly increasing in seconds')
    return value


def description(directory, entry, mesh_sha256):
    path = sibling(directory, entry['file'])
    series = read_limited(path, MANIFEST_BYTES)
    components = series.get('components')
    if (series.get('format') != 'fieldcraft-nodal-series/1'
            or series.get('mesh_sha256') != mesh_sha256 or series.get('time_unit') != 's'
            or series.get('name') != entry['name'] or series.get('unit') != entry['unit']
            or type(components) is not int or components not in (1, 3)):
        raise ValueError('Series header disagrees with the result manifest')
    frames = series.get('frames')
    if not isinstance(frames, list) or not 2 <= len(frames) <= FRAME_LIMIT:
        raise ValueError('A series requires 2 to 4096 frames')
    previous = -math.inf
    used = set()
    for frame in frames:
        previous = next_time(frame['time'], previous)
        filename = frame['file']
        sibling(directory, filename)
        if filename in used:
            raise ValueError('Duplicate series frame filename')
        used.add(filename)
    return path, series


def validate(directory, entry, mesh_sha256, node_ids):
    _, series = description(directory, entry, mesh_sha256)
    count = len(node_ids) * series['components']
    for frame in series['frames']:
        field = read_limited(sibling(directory, frame['file']), FIELD_BYTES)
        if (field.get('format') != 'fieldcraft-nodal-field/1' or field.get('mesh_sha256') != mesh_sha256
                or field.get('name') != series['name'] or field.get('unit') != series['unit']
                or type(field.get('components')) is not int or field['components'] != series['components']):
            raise ValueError('Series frame metadata differs from the series')
        labels, values = field.get('node_ids'), field.get('values')
        if (not isinstance(labels, list) or labels != node_ids
                or any(type(label) is not int for label in labels)):
            raise ValueError('Series frame node labels differ from the exported mesh')
        if (not isinstance(values, list) or len(values) != count
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in values)):
            raise ValueError('Series frame requires one finite value per node and component')


def write(directory, data, catalogue):
    """Consume {name: (unit, iterable of (seconds, array))}, one frame at a time."""
    def save(path, obj, limit):
        with path.open('x', encoding='utf-8') as stream:
            json.dump(obj, stream, separators=(',', ':'), allow_nan=False)
            stream.write('\n')
        if path.stat().st_size > limit:
            raise ValueError(f'Series file exceeds its size limit: {path.name}')

    result = []
    ids = data['node_ids'].tolist()
    digest = str(data['mesh_sha256'].item())
    for number, (name, (unit, snapshots)) in enumerate(catalogue.items(), start=1):
        if not isinstance(name,str) or not name.strip() or not isinstance(unit,str) or not unit.strip():
            raise ValueError('Expected a series name and unit')
        frames = []
        components = None
        previous = -math.inf
        for i, (time, values) in enumerate(snapshots, start=1):
            if i > FRAME_LIMIT:
                raise ValueError('Too many series frames')
            if isinstance(time, np.generic):
                time = time.item()
            previous = next_time(time, previous)
            values = np.asarray(values)
            if (values.shape not in ((len(ids),), (len(ids),3))
                    or values.dtype.kind not in 'fiu' or not np.isfinite(values).all()):
                raise ValueError('Expected finite real scalar or 3-vector values at every mesh node')
            count = 1 if values.ndim == 1 else 3
            if components is not None and components != count:
                raise ValueError('The component count changes between frames')
            components = count
            filename = f'series-{number}-frame-{i:04d}.json'
            save(directory/filename, dict(format='fieldcraft-nodal-field/1', mesh_sha256=digest,
                 name=name, unit=unit, components=count, node_ids=ids, values=values.ravel().tolist()), FIELD_BYTES)
            frames.append({'time':time,'file':filename})
        if len(frames) < 2:
            raise ValueError('A time series needs at least two frames')
        filename = f'series-{number}.json'
        save(directory/filename, dict(format='fieldcraft-nodal-series/1', mesh_sha256=digest,
             name=name, unit=unit, components=components, time_unit='s', frames=frames), MANIFEST_BYTES)
        result.append(dict(name=name, unit=unit, file=filename))
    return result
