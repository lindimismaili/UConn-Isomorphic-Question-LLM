import { existsSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';

function findPythonInterpreter() {
  const repoRoot = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
  const homeDir = os.homedir();

  const candidates = [];

  // Preferred: C:\Users\<you>\venvs\main\Scripts\python.exe
  if (process.platform === 'win32') {
    candidates.push(path.join(homeDir, 'venvs', 'main', 'Scripts', 'python.exe'));
    candidates.push(path.join(repoRoot, '.venv', 'Scripts', 'python.exe'));
  } else {
    candidates.push(path.join(homeDir, 'venvs', 'main', 'bin', 'python'));
    candidates.push(path.join(repoRoot, '.venv', 'bin', 'python'));
  }

  for (const candidate of candidates) {
    if (existsSync(candidate)) return candidate;
  }

  return null;
}

function main() {
  const python = findPythonInterpreter();
  if (!python) {
    console.error(
      [
        'No isolated Python interpreter found.',
        '',
        'Expected one of:',
        `- ${path.join(os.homedir(), 'venvs', 'main', process.platform === 'win32' ? 'Scripts\\python.exe' : 'bin/python')}`,
        `- ${path.join(process.cwd(), '.venv', process.platform === 'win32' ? 'Scripts\\python.exe' : 'bin/python')}`,
        '',
        'Create/activate a venv, then retry.',
      ].join('\n')
    );
    process.exit(1);
  }

  const argsIndex = process.argv.indexOf('--');
  const passthroughArgs = argsIndex === -1 ? [] : process.argv.slice(argsIndex + 1);

  const runPy = path.join(process.cwd(), 'src', 'run.py');

  console.log(`Using Python: ${python}`);
  console.log(`Starting backend: ${runPy} ${passthroughArgs.join(' ')}`.trim());

  const child = spawn(python, [runPy, ...passthroughArgs], {
    stdio: 'inherit',
    env: { ...process.env, PYTHONUTF8: process.env.PYTHONUTF8 ?? '1' },
    windowsHide: false,
  });

  child.on('exit', (code) => {
    process.exit(code ?? 0);
  });
}

main();
