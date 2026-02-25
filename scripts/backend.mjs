import path from 'node:path';
import { spawn } from 'node:child_process';

function resolvePythonCommand() {
  const explicit = (process.env.PYTHON_BIN || process.env.PYTHON || '').trim();
  if (explicit) {
    return { command: explicit, preArgs: [] };
  }

  if (process.platform === 'win32') {
    return { command: 'py', preArgs: ['-3'] };
  }

  return { command: 'python3', preArgs: [] };
}

function main() {
  const argsIndex = process.argv.indexOf('--');
  const passthroughArgs = argsIndex === -1 ? [] : process.argv.slice(argsIndex + 1);

  const runPy = path.join(process.cwd(), 'src', 'run.py');
  const { command, preArgs } = resolvePythonCommand();
  const spawnArgs = [...preArgs, runPy, ...passthroughArgs];

  console.log(`Using Python command: ${command} ${preArgs.join(' ')}`.trim());
  console.log(`Starting backend: ${runPy} ${passthroughArgs.join(' ')}`.trim());

  const child = spawn(command, spawnArgs, {
    stdio: 'inherit',
    env: { ...process.env, PYTHONUTF8: process.env.PYTHONUTF8 ?? '1' },
    windowsHide: false,
  });

  child.on('error', (err) => {
    console.error(`Failed to start backend with ${command}: ${err.message}`);
    process.exit(1);
  });

  child.on('exit', (code) => {
    process.exit(code ?? 0);
  });
}

main();
