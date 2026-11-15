import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

async function login(page: Page, path = "") {
  await page.goto(`/admin/ui/${path}`);
  await page.getByLabel("Admin key", { exact: true }).fill("demo-admin");
  await page.getByRole("button", { name: "Masuk ke dashboard" }).click();
  await expect(page.getByRole("main")).toContainText(
    path
      ? {
          keys: "Akses aplikasi",
          usage: "Distribusi pemakaian",
          requests: "Lalu lintas terbaru",
          prompts: "Log prompt",
          models: "Aktivitas unduhan",
        }[path.replace("/", "")] || "Ringkasan"
      : "Ringkasan provider",
  );
}

test.beforeEach(async ({ request }) => {
  await request.post("/_test/reset");
});

test("invalid credentials and expired sessions return safely to login", async ({
  page,
  request,
}) => {
  await page.goto("/admin/ui/");
  await page.getByLabel("Admin key", { exact: true }).fill("invalid-demo-key");
  await page.getByRole("button", { name: "Masuk ke dashboard" }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "Admin key tidak sesuai." }),
  ).toBeVisible();
  expect(
    await page.evaluate(() =>
      sessionStorage.getItem("ollama-provider-admin-key"),
    ),
  ).toBeNull();
  await page.getByLabel("Admin key", { exact: true }).fill("demo-admin");
  await page.getByRole("button", { name: "Masuk ke dashboard" }).click();
  await expect(
    page.getByRole("heading", { name: "Ringkasan provider", exact: true }),
  ).toBeVisible();
  await request.post("/_test/configure", {
    data: { failures: { status: 401 } },
  });
  await page.getByRole("button", { name: "Sistem normal" }).click();
  await page.getByRole("button", { name: "Periksa kembali" }).click();
  await expect(
    page.getByRole("button", { name: "Masuk ke dashboard" }),
  ).toBeVisible();
  expect(
    await page.evaluate(() =>
      sessionStorage.getItem("ollama-provider-admin-key"),
    ),
  ).toBeNull();
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("empty states, quota and expiry states reflow at a 200-percent desktop equivalent", async ({
  page,
  request,
}) => {
  await page.setViewportSize({ width: 683, height: 450 });
  await request.post("/_test/configure", { data: { empty: true } });
  await login(page, "keys/");
  await expect(
    page.getByText("Belum ada key pada daftar ini", { exact: true }),
  ).toBeVisible();
  for (const [path, title] of [
    ["usage/", "Belum ada pemakaian"],
    ["requests/", "Tidak ada request untuk filter ini"],
    ["prompts/", "Tidak ada prompt untuk filter ini"],
    ["models/", "Siapkan model pertama Anda"],
  ]) {
    await page.goto(`/admin/ui/${path}`);
    await expect(page.getByText(title, { exact: true })).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
    ).toBe(false);
  }
  await request.post("/_test/configure", {
    data: { empty: false, edge_keys: true },
  });
  await page.goto("/admin/ui/keys/");
  await expect(
    page
      .getByRole("article")
      .filter({ hasText: "client-" })
      .getByText("Kedaluwarsa", { exact: true }),
  ).toBeVisible();
  await expect(
    page
      .getByRole("article")
      .filter({ hasText: "asisten-internal" })
      .getByText("Kuota habis", { exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
  ).toBe(false);
  await page.goto("/admin/ui/models/");
  await expect(
    page.getByRole("heading", {
      name: "model-" + "x".repeat(190),
      exact: true,
    }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
  ).toBe(false);
});

test("all pages load under CSP, navigation history and deep links work", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (/Content Security Policy|violates.*directive/i.test(message.text()))
      errors.push(message.text());
  });
  await login(page);
  await expect(
    page.getByRole("heading", { name: "Aktivitas request", exact: true }),
  ).toBeVisible();
  for (const [label, heading] of [
    ["API key", "Akses aplikasi"],
    ["Pemakaian", "Distribusi pemakaian"],
    ["Request", "Lalu lintas terbaru"],
    ["Log prompt", "Telusuri percakapan"],
    ["Model", "Aktivitas unduhan"],
  ]) {
    await page.getByRole("link", { name: label, exact: true }).click();
    await expect(
      page.getByRole("heading", { name: heading, exact: true }),
    ).toBeVisible();
  }
  await page.goBack();
  await expect(page).toHaveURL(/\/prompts\/$/);
  await page.goForward();
  await expect(page).toHaveURL(/\/models\/$/);
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Aktivitas unduhan", exact: true }),
  ).toBeVisible();
  await page.goto("/admin/ui#keys");
  await expect(page).toHaveURL(/\/keys\/$/);
  await expect(
    page.getByRole("heading", { name: "Akses aplikasi", exact: true }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});

test("one-time secret, duplicate submit prevention, validation and partial PATCH", async ({
  page,
  request,
}) => {
  await login(page, "keys/");
  await page.getByRole("button", { name: "Buat API key", exact: true }).click();
  const dialog = page.getByRole("dialog", {
    name: "Buat API key",
    exact: true,
  });
  await dialog.getByLabel("Nama client", { exact: true }).fill("client-baru");
  await request.post("/_test/configure", { data: { delay: 350 } });
  await dialog
    .getByRole("button", { name: "Buat key", exact: true })
    .evaluate((button: HTMLButtonElement) => {
      button.form?.requestSubmit();
      button.form?.requestSubmit();
    });
  await expect(
    page.getByRole("dialog", { name: /Key untuk client-baru/ }),
  ).toBeVisible();
  expect(
    (await (await request.get("/_test/inspect")).json()).counts["POST /keys"],
  ).toBe(1);
  await expect(
    page.getByText("sk-demo-new-secret", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Sudah disimpan" }).click();
  await expect(
    page.getByText("sk-demo-new-secret", { exact: true }),
  ).toHaveCount(0);
  await request.post("/_test/configure", { data: { delay: 0 } });
  await page
    .getByRole("button", { name: "Ubah aplikasi-chat", exact: true })
    .click();
  const edit = page.getByRole("dialog", {
    name: "Ubah key aplikasi-chat",
    exact: true,
  });
  await edit.getByLabel("Batas per menit", { exact: true }).fill("-1");
  await edit.getByRole("button", { name: "Simpan perubahan" }).click();
  expect(
    (await (await request.get("/_test/inspect")).json()).counts[
      "PATCH /keys/1"
    ],
  ).toBeUndefined();
  await expect(
    edit.getByLabel("Batas per menit", { exact: true }),
  ).toBeFocused();
  await edit.getByLabel("Batas per menit", { exact: true }).fill("35");
  await edit.getByRole("button", { name: "Simpan perubahan" }).click();
  await expect(edit).not.toBeVisible();
  expect(
    (await (await request.get("/_test/inspect")).json()).last_patch,
  ).toEqual({ rpm_limit: 35 });
});

test("server validation errors identify and focus their field", async ({
  page,
  request,
}) => {
  await login(page, "keys/");
  await request.post("/_test/configure", {
    data: { failures: { "keys/1": 422 } },
  });
  await page
    .getByRole("button", { name: "Ubah aplikasi-chat", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Ubah key aplikasi-chat",
    exact: true,
  });
  await dialog.getByLabel("Batas per menit", { exact: true }).fill("70");
  await dialog.getByRole("button", { name: "Simpan perubahan" }).click();
  await expect(
    dialog.getByText("Nilai harus minimal 1.", { exact: true }),
  ).toBeVisible();
  await expect(
    dialog.getByLabel("Batas per menit", { exact: true }),
  ).toBeFocused();
  await expect(dialog).toBeVisible();
});

test("revoking keys, model enable and delete, and pull progress", async ({
  page,
}) => {
  await login(page, "keys/");
  await page
    .getByRole("button", { name: "Cabut aplikasi-chat", exact: true })
    .click();
  const confirmation = page.getByRole("dialog", {
    name: "Cabut key aplikasi-chat?",
  });
  await confirmation
    .getByRole("button", { name: "Cabut key", exact: true })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "Key dicabut." }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Model", exact: true }).click();
  await page.getByLabel("Aktifkan gemma3:4b", { exact: true }).uncheck();
  await expect(
    page.getByLabel("Aktifkan gemma3:4b", { exact: true }),
  ).not.toBeChecked();
  await page
    .getByRole("button", { name: "Hapus qwen3.5:4b", exact: true })
    .click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Hapus model", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "qwen3.5:4b", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Unduh model", exact: true }).click();
  await page.getByLabel("Nama model", { exact: true }).fill("nomic-embed-text");
  await page.getByRole("button", { name: "Mulai unduhan" }).click();
  await expect(
    page.getByText("nomic-embed-text", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("progressbar", { name: "Progres unduhan nomic-embed-text" }),
  ).toHaveAttribute("value", "37");
});

test("prompts paginate, load detail on demand, and keep manual refresh", async ({
  page,
  request,
}) => {
  await page.clock.install();
  await login(page, "prompts/");
  await expect(
    page.getByRole("button", { name: /^Lihat prompt / }),
  ).toHaveCount(50);
  expect(
    (await (await request.get("/_test/inspect")).json()).counts[
      "GET /prompts/120"
    ],
  ).toBeUndefined();
  await page.getByRole("button", { name: "Muat 50 lagi" }).click();
  await expect(
    page.getByRole("button", { name: /^Lihat prompt / }),
  ).toHaveCount(100);
  await page.getByRole("button", { name: "Muat 50 lagi" }).click();
  await expect(
    page.getByRole("button", { name: /^Lihat prompt / }),
  ).toHaveCount(120);
  await page
    .getByRole("button", { name: "Lihat prompt 120", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText(
    "Antrean mengatur akses sesuai kapasitas GPU.",
  );
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("button", { name: "Lihat prompt 120", exact: true }),
  ).toBeFocused();
  const before = (await (await request.get("/_test/inspect")).json()).counts[
    "GET /prompts"
  ];
  await page.clock.fastForward(21000);
  await page.evaluate(() =>
    document.dispatchEvent(new Event("visibilitychange")),
  );
  await expect(
    page.getByRole("button", { name: /^Lihat prompt / }),
  ).toHaveCount(120);
  expect(
    (await (await request.get("/_test/inspect")).json()).counts["GET /prompts"],
  ).toBe(before);
  await page.getByLabel("Cari teks", { exact: true }).fill("ab");
  await page.getByRole("button", { name: "Terapkan" }).click();
  await expect(page.getByLabel("Cari teks", { exact: true })).toBeFocused();
});

test("untrusted API text stays inert, and logout cancels pending work", async ({
  page,
  request,
}) => {
  await request.post("/_test/configure", { data: { inject: true } });
  await login(page, "keys/");
  await expect(
    page
      .getByText('<img src=x onerror="window.__xss=1">', { exact: true })
      .first(),
  ).toBeVisible();
  expect(
    await page.evaluate(() => (window as Window & { __xss?: number }).__xss),
  ).toBeUndefined();
  await page.getByRole("link", { name: "Log prompt", exact: true }).click();
  await page
    .getByRole("button", { name: "Lihat prompt 120", exact: true })
    .click();
  await expect(
    page.getByText("<script>window.__xss=1</script>", { exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => (window as Window & { __xss?: number }).__xss),
  ).toBeUndefined();
  await page.keyboard.press("Escape");
  await request.post("/_test/configure", { data: { delay: 600 } });
  await page.getByRole("link", { name: "Pemakaian", exact: true }).click();
  await page.getByRole("button", { name: "Keluar", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Masuk ke dashboard" }),
  ).toBeVisible();
  await expect(page.getByRole("main")).not.toContainText(
    "Distribusi pemakaian",
  );
  expect(
    await page.evaluate(() =>
      sessionStorage.getItem("ollama-provider-admin-key"),
    ),
  ).toBeNull();
});

test("connection failures are persistent and retry restores the screen", async ({
  page,
  request,
}) => {
  await login(page, "requests/");
  await request.post("/_test/configure", {
    data: { failures: { "usage/recent": 500 } },
  });
  await page.getByRole("button", { name: "Perbarui", exact: true }).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText(
    "Data belum dapat diperbarui",
  );
  await expect(page.getByRole("table")).toBeVisible();
  await request.post("/_test/configure", { data: { failures: {} } });
  await page.getByRole("button", { name: "Coba lagi", exact: true }).click();
  await expect(page.getByRole("main").getByRole("alert")).toHaveCount(0);
});

for (const theme of ["light", "dark"] as const) {
  for (const width of [390, 768, 1366]) {
    test(`responsive screenshots and accessibility ${width}px ${theme}`, async ({
      page,
    }, testInfo) => {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
      await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
      const errors: string[] = [];
      page.on("pageerror", (error) => errors.push(error.message));
      page.on("console", (message) => {
        if (/Content Security Policy|violates.*directive/i.test(message.text()))
          errors.push(message.text());
      });
      await login(page);
      for (const path of [
        "",
        "keys/",
        "usage/",
        "requests/",
        "prompts/",
        "models/",
      ]) {
        if (path) await page.goto(`/admin/ui/${path}`);
        await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
        await expect(
          page.getByRole("button", { name: "Masuk ke dashboard" }),
        ).toHaveCount(0);
        await expect(page.locator(".loading-grid")).toHaveCount(0);
        const overflow = await page.evaluate(
          () => document.documentElement.scrollWidth > innerWidth,
        );
        expect(overflow, `overflow at ${path} ${width}`).toBe(false);
        const results = await new AxeBuilder({ page })
          .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
          .analyze();
        expect(
          results.violations.map((item) => ({
            id: item.id,
            nodes: item.nodes.map((node) => node.target),
          })),
        ).toEqual([]);
        if (path === "" || path === "keys/" || path === "prompts/")
          await page.screenshot({
            path: testInfo.outputPath(
              `${path.replace("/", "") || "overview"}-${width}-${theme}.png`,
            ),
            fullPage: true,
            animations: "disabled",
          });
      }
      if (width === 390) {
        await page.getByRole("button", { name: "Buka menu" }).click();
        await expect(
          page.getByRole("dialog", { name: "Menu workspace" }),
        ).toBeVisible();
        await page
          .getByRole("dialog")
          .getByRole("link", { name: "API key", exact: true })
          .click();
        await expect(
          page.getByRole("heading", { name: "API key", exact: true }),
        ).toBeVisible();
      }
      expect(errors).toEqual([]);
    });
  }
}

test("theme preferences persist and named modal restores focus", async ({
  page,
}) => {
  await login(page, "keys/");
  await page.getByRole("button", { name: "Preferensi tampilan" }).click();
  await page.getByLabel("Tema", { exact: true }).selectOption("dark");
  await page.getByLabel("Aktifkan animasi", { exact: true }).uncheck();
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("button", { name: "Preferensi tampilan" }),
  ).toBeFocused();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.locator("html")).toHaveAttribute("data-motion", "off");
});
