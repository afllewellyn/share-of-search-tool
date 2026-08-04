# Vendored dependencies

## Chart.js 4.4.1

`chart.umd.min.js` is the unmodified UMD build of [Chart.js](https://www.chartjs.org/)
v4.4.1, taken from the `chart.js` npm package (`dist/chart.umd.js`).

It is vendored rather than loaded from a CDN so the generated dashboard is
genuinely self-contained: the HTML file renders with no network access at all,
which is what makes it safe to double-click from a downloads folder or forward
as an email attachment.

Chart.js is MIT licensed. The full licence text is in `LICENSE-chart.js.md`.

To update: download `chart.js-<version>.tgz` from the npm registry, copy
`package/dist/chart.umd.js` over `chart.umd.min.js`, copy `package/LICENSE.md`
over `LICENSE-chart.js.md`, and update the version above and in
`sos/dashboard/build.py`.
