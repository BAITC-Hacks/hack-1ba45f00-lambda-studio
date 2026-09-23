import { spawn } from 'node:child_process';
const python = process.env.PYTHON || 'python';
const child = spawn(python, ['-m', 'mycelium.serve'], { cwd: new URL('../../', import.meta.url), stdio: 'inherit' });
child.on('error', () => { console.error('Python не найден. Запустите python -m mycelium.serve из активированного окружения проекта.'); process.exitCode = 1; });
child.on('exit', code => { process.exitCode = code ?? 1; });
