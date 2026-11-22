"""Show a validated run in a FieldCraft desktop instance on its selected loopback control port."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import socket
import sys
import time
import zipfile


class ControlError(ValueError):
    pass


def read_json(path, limit):
    with path.open('rb') as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise ValueError(f'Datei überschreitet das Größenlimit: {path}')
    return json.loads(content)


class Control:
    def __init__(self, port):
        if not 1 <= port <= 65535:
            raise ValueError('Port muss zwischen 1 und 65535 liegen.')
        self.port = port
        self.request_id = 0

    def __call__(self, method, params):
        self.request_id += 1
        message = {'id': self.request_id, 'method': method, 'params': params}
        with socket.create_connection(('127.0.0.1', self.port), timeout=5) as connection:
            connection.settimeout(65)
            connection.sendall(json.dumps(message, allow_nan=False).encode() + b'\n')
            with connection.makefile('rb') as reader:
                reply = reader.readline(16 * 1024**2 + 1)
        if not reply.endswith(b'\n') or len(reply) > 16 * 1024**2:
            raise ControlError('Control-Antwort fehlt oder überschreitet 16 MiB.')
        response = json.loads(reply)
        if response.get('id') != self.request_id:
            raise ControlError('Die Control-Antwort gehört zu einer anderen Anfrage.')
        if response.get('ok') is not True:
            raise ControlError(str(response.get('error', 'FieldCraft hat die Anfrage abgelehnt.')))
        return response['result']


def read_run(path):
    manifest = (path / 'results.json' if path.is_dir() else path).resolve(strict=True)
    result = read_json(manifest, 4 * 1024**2)
    if result.get('format') != 'fieldcraft-external-run/1' or not re.fullmatch('[0-9a-f]{64}', result.get('mesh_sha256', '')):
        raise ValueError('Eine geprüfte results.json mit Netz-SHA-256 wird benötigt.')
    return manifest, result


def select_field(manifest, result, name, component):
    matches = [f for f in result['fields'] if f['name'] == name]
    if len(matches) != 1:
        raise ValueError(f'Feldname fehlt oder ist mehrdeutig: {name!r}. --list zeigt die Namen.')
    selected = matches[0]
    path = (manifest.parent / selected['file']).resolve(strict=True)
    if path.parent != manifest.parent:
        raise ValueError('Die Feld-Datei muss im selben Verzeichnis wie results.json liegen.')
    field = read_json(path, 64 * 1024**2)
    if (field.get('format') != 'fieldcraft-nodal-field/1' or
            (field.get('mesh_sha256'), field.get('name'), field.get('unit')) !=
            (result['mesh_sha256'], name, selected['unit'])):
        raise ValueError('Feld-Datei und Ergebnismanifest stimmen nicht überein.')
    count = field.get('components')
    if count not in (1, 3) or (component and count != 3):
        raise ValueError('Eine Komponente kann nur für ein Vektorfeld ausgewählt werden.')
    return {'path': str(path), **({'component': component} if component else {})}


def validate_model(path, expected_hash):
    if path.suffix.lower() != '.fieldcraft':
        raise ValueError('Eine native .fieldcraft-Datei wird benötigt.')
    with zipfile.ZipFile(path) as archive:
        parts = [item for item in archive.infolist() if item.filename == 'mesh.bin']
        if len(parts) != 1 or parts[0].file_size > 256 * 1024**2:
            raise ValueError('Ungültiges mesh.bin im Modellarchiv.')
        with archive.open(parts[0]) as part:
            actual_hash = hashlib.file_digest(part, 'sha256').hexdigest()
    if actual_hash != expected_hash:
        raise ValueError('Das Modell auf dem Datenträger hat ein anderes Netz als der Ergebnislauf.')


def idle(state):
    if state.get('prompt') is not None:
        raise ControlError(f'FieldCraft wartet auf eine Entscheidung im Fenster: {state["prompt"]}. Danach erneut ausführen.')
    if state.get('dialog') or state.get('file_picker'):
        raise ControlError('Im FieldCraft-Fenster ist noch ein Dialog geöffnet.')


def open_model(call, model):
    state = call('ui.inspect', {})
    idle(state)
    if state.get('busy') or state.get('job'):
        raise ControlError('Im ausgewählten Fenster läuft bereits eine Aufgabe.')
    # A real drop enters the normal confirm-replace flow. engine.execute(file.open)
    # would replace the session without offering to save its unsaved edits.
    reply = call('ui.drop', {'paths': [str(model)]})
    if reply.get('dropped') != 1:
        raise ControlError('Das Fenster hat die Modelldatei nicht angenommen.')
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        time.sleep(0.12)
        state = call('ui.inspect', {})
        idle(state)
        if state.get('job') or state.get('busy'):
            continue
        status = state.get('status') or {}
        if status.get('error'):
            raise ControlError(str(status.get('text', 'Öffnen fehlgeschlagen.')))
        recent = state.get('recent') or []
        if recent and Path(recent[0]).resolve() == model:
            return
    raise ControlError('Das Fenster hat das Öffnen des Modells nicht innerhalb von 120 Sekunden bestätigt.')


def apply(call, manifest, run, field, component, integrals, model, series=None):
    actions = []
    if field:
        actions.append(('results.external.contour', select_field(manifest, run, field, component)))
    if series:
        from time_series import description
        matches = [entry for entry in run.get('series', []) if entry['name'] == series]
        if len(matches) != 1 or field:
            raise ValueError('Select one field or a uniquely named time series.')
        path, item = description(manifest.parent, matches[0], run['mesh_sha256'])
        if component and item['components'] != 3:
            raise ValueError('A component requires a vector series.')
        actions.append(('results.external.series', {'path':str(path), **({'component': component} if component else {})}))
    if integrals:
        actions.append(('results.external.integrals', {'path': str(manifest)}))
    if not actions:
        raise ValueError('Ein Feld oder --integrals auswählen.')
    available = {entry['id'] for entry in call('engine.commands', {})}
    missing = [cmd for cmd, _ in actions if cmd not in available]
    if missing:
        raise ControlError('Dieser FieldCraft-Build unterstützt die ausgewählten Befehle noch nicht: ' + ', '.join(missing))
    if model is not None:
        validate_model(model, run['mesh_sha256'])
        open_model(call, model)
    else:
        state = call('ui.inspect', {})
        idle(state)
        if state.get('busy') or state.get('job'):
            raise ControlError('Im ausgewählten Fenster läuft eine Aufgabe.')
    results = []
    for command, params in actions:
        # Each native command re-encodes the CURRENT in-memory mesh and checks the
        # SHA-256 before changing the view or returning any integral quantities.
        results.append(call('engine.execute', {'command': command, 'params': params}))
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path, help='Laufordner oder results.json')
    parser.add_argument('--list', action='store_true', help='Felder und Integralnamen lokal auflisten')
    parser.add_argument('--port', type=int, help='Bewusst ausgewählter Port des laufenden FieldCraft-Fensters')
    display = parser.add_mutually_exclusive_group()
    display.add_argument('--series', help='Show a named time series on its original mesh')
    display.add_argument('--field', help='Exakter Feldname aus --list')
    parser.add_argument('--component', choices=('x','y','z','magnitude'), help='Vektorkomponente, Standard ist der Betrag')
    parser.add_argument('--integrals', action='store_true', help='Integrale im aktuellen Modell prüfen und anzeigen')
    parser.add_argument('--open-model', action='store_true', help='Bei Bedarf das Modell aus dem Manifest über die normale Öffnen-Funktion laden')
    parser.add_argument('--model', type=Path, help='Mit --open-model einen verschobenen Modellpfad angeben')
    args = parser.parse_args(argv)
    manifest, run = read_run(args.run)
    if args.component and not (args.field or args.series):
        parser.error('--component benötigt --field oder --series.')
    if args.model and not args.open_model:
        parser.error('--model benötigt --open-model.')
    if args.list or (not args.field and not args.integrals and not args.series):
        print(json.dumps({'fields': [{k:f[k] for k in ('name','unit')} for f in run['fields']],
                          'series': [{k:s[k] for k in ('name','unit')} for s in run.get('series',[])],
                          'integrals': [{k:q[k] for k in ('name','unit')} for q in run['integrals']]},
                         ensure_ascii=False, indent=2))
    if not args.field and not args.integrals and not args.series:
        if args.open_model:
            parser.error('--open-model benötigt --field oder --integrals.')
        return
    if args.port is None:
        parser.error('Für die Anzeige im Fenster --port angeben.')
    model = (args.model or Path(run['model'])).resolve(strict=True) if args.open_model else None
    replies = apply(Control(args.port), manifest, run, args.field, args.component, args.integrals, model, series=args.series)
    for reply in replies:
        print(json.dumps(reply, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        sys.exit(str(error))
