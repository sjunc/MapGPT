MAPGPT frontend Demo Mode files (local only)

1. Put scripts/create-demo.mjs into your project at scripts/create-demo.mjs
2. Put src/demo.ts into your project at src/demo.ts
3. Download a Seoul boundary GeoJSON to public/demo/seoul-boundaries-2015.geojson:
   https://raw.githubusercontent.com/southkorea/seoul-maps/master/juso/2015/json/seoul_neighborhoods_geo_simple.json
   Source: southkorea/seoul-maps, Apache-2.0; original public administrative data as described in their README.
4. Run `node scripts/create-demo.mjs` to write public/demo/analysis.json
5. Modify App.tsx per ChatGPT message and load http://localhost:5173/?demo=1

Demo statistics are synthetically generated and must not be presented as real analysis.
2015 legal-dong boundaries are not current administrative-dong boundaries.
No Git commands, commits, or remote writes are performed by this package.
