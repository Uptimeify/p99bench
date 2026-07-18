// @ts-check
import { defineConfig } from 'astro/config';

// GitHub Pages project site: served at https://uptimeify.github.io/p99bench/
// `base` must match the repo name or every asset and link 404s on Pages.
export default defineConfig({
  site: 'https://uptimeify.github.io',
  base: '/p99bench',
  output: 'static',
  trailingSlash: 'ignore',
});
