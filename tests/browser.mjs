import { strict as assert } from 'node:assert';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const baseURL = process.env.FADLAB_BROWSER_URL || 'http://127.0.0.1:5089';
const email = process.env.FADLAB_BROWSER_ADMIN;
const password = process.env.FADLAB_BROWSER_PASSWORD;
assert(email && password, 'Especifique uma conta ADMIN num banco de teste descartável.');
const widthSet = [320, 360, 375, 390, 414, 768, 1024, 1440];
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_BINARY || undefined,
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
});

try {
  const context = await browser.newContext({ baseURL, viewport: { width: 390, height: 844 } });
  const page = await context.newPage();
  const exceptions = [];
  page.on('pageerror', e => exceptions.push(e.message));
  await page.goto('/entrar');
  await page.getByLabel('E-mail').fill(email);
  await page.getByLabel('Senha', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Entrar', exact: true }).click();
  await page.waitForURL('/');
  assert.equal(await page.getByRole('heading', { name: 'Próximos eventos' }).count(), 1);

  const marker = 'Ministério ' + Date.now();
  await page.goto('/painel/ministerios/novo');
  await page.getByLabel('Nome', { exact: true }).fill(marker);
  await page.getByRole('button', { name: 'Salvar', exact: true }).click();
  await page.waitForURL('/painel/ministerios');
  assert.equal(await page.getByRole('heading', { name: marker }).count(), 1);

  await page.goto('/painel/agenda/novo');
  await page.getByLabel('Ministério').selectOption({ label: marker });
  await page.getByLabel('Título').fill('Ensaio ' + marker);
  await page.getByLabel('Início').fill('2030-05-10T18:00');
  await page.getByLabel('Fim').fill('2030-05-10T20:00');
  await page.getByRole('button', { name: 'Salvar', exact: true }).click();
  await page.waitForURL('/painel/agenda');
  assert.equal(await page.getByRole('heading', { name: 'Ensaio ' + marker }).count(), 1);

  const paths = ['/', '/painel/agenda', '/painel/agenda?view=calendar&month=2030-05',
    '/painel/escalas', '/mais', '/perfil', '/administracao'];
  for (const width of widthSet) {
    await page.setViewportSize({ width, height: 844 });
    for (const path of paths) {
      const response = await page.goto(path);
      assert.equal(response.status(), 200, path);
      const fits = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
      assert(fits, 'Rolagem horizontal indevida em ' + width + ' px em ' + path);
      const menu = width < 900 ? 'Navegação principal' : 'Áreas do FadLab';
      assert(await page.getByRole('navigation', { name: menu, exact: true }).isVisible());
    }
  }
  await page.goto('/perfil');
  await page.getByLabel('Aparência').selectOption('dark');
  await page.getByRole('button', { name: 'Salvar perfil' }).click();
  await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark');
  await page.evaluate(() => navigator.serviceWorker.ready);
  await page.waitForFunction(() => !!navigator.serviceWorker.controller);
  const cached = await page.evaluate(async () => {
    const keys = await caches.keys();
    return (await Promise.all(keys.map(async (key) => (await (await caches.open(key)).keys())
      .map(request => new URL(request.url).pathname)))).flat();
  });
  assert(cached.includes('/offline.html'));
  assert(cached.includes('/assets/app.css'));
  assert(cached.every(path => path.startsWith('/assets/') || path === '/offline.html'));
  await context.setOffline(true);
  await page.goto('/painel/agenda');
  assert(await page.getByRole('heading', { name: 'Sem conexão' }).isVisible());
  await context.setOffline(false);
  await page.goto('/perfil');
  await page.getByRole('button', { name: 'Sair da conta' }).click();
  await page.waitForURL('/entrar');
  assert.deepEqual(exceptions, []);
  console.log(`UI verificada em ${widthSet.length} larguras e ${paths.length} rotas. Fluxos e cache PWA aprovados.`);
} finally {
  await browser.close();
}
