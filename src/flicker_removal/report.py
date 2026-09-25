"""Self-contained local comparison index; images stay on this computer."""

from pathlib import Path
import html
import json
import os
from urllib.parse import quote


def write_gallery(root, summary):
    root = Path(root)
    rows = []
    for r in summary["reports"]:
        stem = Path(r["file"]).stem
        rows.append(
            {
                "name": stem,
                "status": r["status"],
                "shutter": f"1/{round(1 / r['shutter_s'])}",
                "before": r["before"]["luminance"],
                "after": r["after"]["luminance"],
                "gain": [r["gain_min"], r["gain_max"]],
                "review": r.get("review_reasons", []),
            }
        )
    data = json.dumps(rows).replace("</", "<\\/")
    extra = []
    documentation = Path(__file__).resolve().parents[2] / "README.md"
    if documentation.exists():
        extra.append(
            '<a href="'
            + html.escape(quote(os.path.relpath(documentation, root)))
            + '">Documentation</a>'
        )
    if (root / "evaluation" / "methods.html").exists():
        extra.append('<a href="evaluation/methods.html">Four-method comparison</a>')
    page = (
        """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Flicker removal · local comparison</title>
<style>body{margin:0;background:#15191e;color:#e8edf3;font:15px system-ui}main{max-width:1500px;margin:auto;padding:24px}h1{font-size:26px;margin-bottom:8px}p{color:#b5c1cf;line-height:1.6}button,select{background:#28313c;color:#eef;border:1px solid #4a5867;padding:10px;border-radius:6px}nav{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:18px 0}a{color:#8fc7ff}#stage{position:relative;width:100%;aspect-ratio:1.5;background:#090c10;overflow:hidden}#stage img{position:absolute;width:100%;height:100%;object-fit:contain}#top{position:absolute;inset:0;clip-path:inset(0 50% 0 0)}#top img{width:100%;height:100%}#line{position:absolute;left:50%;top:0;bottom:0;border-left:2px solid white;pointer-events:none}input[type=range]{width:100%;margin:15px 0}label{font-weight:600}.labels{display:flex;justify-content:space-between;margin:8px 0}.meta{padding:12px;background:#212932;border-radius:8px;line-height:1.7}footer{padding-top:20px;font-size:13px;color:#9cabbd}.links{display:flex;gap:20px;flex-wrap:wrap}.badge{color:#92d3bc}</style>
<main><h1>Flicker removal</h1><p>PHOTO_COUNT RAW photos · one shared lighting profile · smooth Bayer gain correction. Original RAW files are preserved. Use the slider to compare matched neutral renders; filenames marked “unchanged” passed through without correction.</p>
<nav><button id="prev">← Previous</button><select id="picker" aria-label="Photo"></select><button id="next">Next →</button><span id="count"></span></nav>
<div class="labels"><label>Original</label><label>Physics correction</label></div>
<div id="stage"><img id="after" alt="Corrected RAW neutral render"><div id="top"><img id="before" alt="Original RAW neutral render"></div><div id="line"></div></div>
<input id="slider" type="range" min="0" max="100" value="50" aria-label="Before and after split">
<div id="meta" class="meta"></div><p class="links"><a id="dng">Corrected DNG</a><a id="original">Original preview</a><a id="corrected">Corrected preview</a><a href="batch-report.json">Full measurements</a>EXTRA_LINKS</p>
<footer>The row score is a diagnostic, not ground truth or a percentage of all visible banding removed. Scene detail can contribute to it. No neural image output is used in these DNGs. Evaluation of Flickerformer is separate.</footer></main>
<script>const rows=DATA;let index=0;const $=id=>document.getElementById(id);rows.forEach((r,i)=>{$('picker').add(new Option(r.name+' · '+r.shutter+' · '+r.status.replaceAll('_',' '),i))});function show(i){index=(i+rows.length)%rows.length;const r=rows[index];const key=encodeURIComponent(r.name);$('picker').value=index;$('count').textContent=(index+1)+' / '+rows.length;$('before').src='previews/neutral-original/'+key+'.jpg';$('after').src='previews/physics/'+key+'.jpg';$('dng').href='corrected-raw/'+key+'_deflicker.dng';$('original').href=$('before').src;$('corrected').href=$('after').src;$('meta').textContent=r.name+' · '+r.shutter+' · '+r.status.replaceAll('_',' ')+' | Luminance row score '+r.before.toFixed(4)+' → '+r.after.toFixed(4)+' | Gain '+r.gain[0].toFixed(3)+'–'+r.gain[1].toFixed(3)+(r.review.length?' | Review: '+r.review.join(', '):'');}function split(){let s=$('slider').value;$('top').style.clipPath='inset(0 '+(100-s)+'% 0 0)';$('line').style.left=s+'%';}$('picker').onchange=e=>show(+e.target.value);$('prev').onclick=()=>show(index-1);$('next').onclick=()=>show(index+1);$('slider').oninput=split;document.addEventListener('keydown',e=>{if(e.key==='ArrowRight')show(index+1);if(e.key==='ArrowLeft')show(index-1)});show(Math.max(0,rows.findIndex(r=>r.status==='corrected')));</script></html>""".replace(
            "DATA", data
        )
        .replace("EXTRA_LINKS", "".join(extra))
        .replace("PHOTO_COUNT", str(len(rows)))
    )
    (root / "comparison.html").write_text(page)


def write_version_gallery(
    v1_root,
    v2_root,
    v1_summary=None,
    v2_summary=None,
    *,
    contact_sheets=(),
    featured=(),
    details=(),
):
    """Write an optional original/V1/V2 viewer without changing the V1 gallery.

    Source previews are linked directly, so no comparison image is inferred
    from gains or substituted for an actual RAW render. The standalone builder
    validates the files and matched rendering settings before calling this.
    """
    v1_root, v2_root = Path(v1_root).resolve(), Path(v2_root).resolve()
    if v1_summary is None:
        v1_summary = json.loads((v1_root / "batch-report.json").read_text())
    if v2_summary is None:
        v2_summary = json.loads((v2_root / "batch-report.json").read_text())
    first = {Path(row["file"]).stem: row for row in v1_summary["reports"]}
    second = {Path(row["file"]).stem: row for row in v2_summary["reports"]}
    if len(first) != len(v1_summary["reports"]) or len(second) != len(v2_summary["reports"]):
        raise ValueError("Duplicate photo names in a batch report")
    if not first or set(first) != set(second):
        raise ValueError("V1 and V2 must contain the same nonempty photo set")

    def url(path):
        return quote(os.path.relpath(Path(path).resolve(), v2_root))

    rows = []
    for name, before in first.items():
        after = second[name]
        refinement = after.get("scene_refinement", {})
        refined = refinement.get("status") == "polished"
        if refined:
            refinement_label = "Refined using this session"
        elif after["status"] == "corrected":
            refinement_label = (
                "V1 correction retained" if before["status"] == "corrected" else "Corrected in V2"
            )
        else:
            refinement_label = "Original retained"
        rows.append(
            {
                "name": name,
                "shutter": f"1/{round(1 / after['shutter_s'])}",
                "v1Status": before["status"],
                "v2Status": after["status"],
                "refinement": refinement_label,
                "refined": refined,
                "refinementReason": refinement.get("reason", "no_session_refinement"),
                "scores": [
                    before["before"]["luminance"],
                    before["after"]["luminance"],
                    after["after"]["luminance"],
                ],
                "images": {
                    "original": url(v1_root / "previews/neutral-original" / f"{name}.jpg"),
                    "v1": url(v1_root / "previews/physics" / f"{name}.jpg"),
                    "v2": url(v2_root / "previews/physics" / f"{name}.jpg"),
                },
                "downloads": {
                    "v1Dng": url(v1_root / "corrected-raw" / f"{name}_deflicker.dng"),
                    "v2Dng": url(v2_root / "corrected-raw" / f"{name}_deflicker.dng"),
                    "v1Jpeg": url(v1_root / "corrected-jpeg" / f"{name}_deflicker.jpg"),
                    "v2Jpeg": url(v2_root / "corrected-jpeg" / f"{name}_deflicker.jpg"),
                    "v1Report": url(v1_root / "reports" / f"{name}.json"),
                    "v2Report": url(v2_root / "reports" / f"{name}.json"),
                },
            }
        )
    extras = []
    for i, path in enumerate(contact_sheets, 1):
        extras.append(f'<a href="{html.escape(url(path))}">Contact sheet {i}</a>')
    for path in featured:
        name = Path(path).stem.removeprefix("featured-")
        extras.append(f'<a href="{html.escape(url(path))}">{html.escape(name)} comparison</a>')
    for path in details:
        name = Path(path).stem.removeprefix("detail-")
        extras.append(f'<a href="{html.escape(url(path))}">{html.escape(name)} · 100% detail</a>')
    data = json.dumps(rows, ensure_ascii=True).replace("</", "<\\/")
    page = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Flicker removal · V1 and V2</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#13171c;color:#edf1f5;font:15px system-ui,sans-serif}main{max-width:1900px;margin:auto;padding:24px}h1{font-size:27px;margin:0 0 10px}p{line-height:1.6;color:#b9c4cf}nav,.views,.links{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:16px 0}button,select{font:inherit;padding:10px 13px;background:#252e39;color:#eef3f8;border:1px solid #536173;border-radius:6px}button{cursor:pointer}button[aria-pressed=true]{background:#284959;border-color:#8ccacb}a{color:#9acfff}#panels{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;align-items:start}#panels[data-view=all]{grid-template-columns:repeat(3,minmax(0,1fr))}#panels[data-view=original],#panels[data-view=v1],#panels[data-view=v2]{grid-template-columns:1fr}figure{margin:0;background:#090c10;border:1px solid #303b48}figure[hidden]{display:none}figcaption{padding:10px 12px;font-weight:600}figure img{display:block;width:100%;height:auto;max-height:75vh;object-fit:contain}#meta{padding:14px;background:#202831;border-radius:8px;line-height:1.8;margin:16px 0}#scores{color:#bac9d8}.links a{padding:3px 0;margin-right:14px}.downloads{border-top:1px solid #354151;padding-top:8px}footer{margin-top:25px;color:#aab8c5;font-size:13px;line-height:1.6}small{color:#aab8c5}#refinement{font-weight:600;color:#a9dcc8}@media(max-width:760px){main{padding:14px}#panels,#panels[data-view=all]{grid-template-columns:1fr}figure img{max-height:none}select{max-width:100%}}
</style></head><body><main>
<h1>Flicker removal: V1 and V2</h1>
<p>COUNT matched neutral renders. Compare the original, the first correction, and the session refinement. Open a preview to inspect it at full size.</p>
<nav aria-label="Photo navigation"><button id="prev">← Previous</button><select id="picker" aria-label="Photo"></select><button id="next">Next →</button><span id="count"></span></nav>
<div class="views" role="group" aria-label="Comparison view"><button data-view="compare" aria-pressed="true">V1 / V2</button><button data-view="all" aria-pressed="false">All three</button><button data-view="original" aria-pressed="false">Original</button><button data-view="v1" aria-pressed="false">V1</button><button data-view="v2" aria-pressed="false">V2</button></div>
<div id="panels" data-view="compare">
<figure id="panel-original" hidden><figcaption>Original</figcaption><a id="preview-original"><img id="image-original" alt="Original RAW neutral render"></a></figure>
<figure id="panel-v1"><figcaption>V1 · first correction</figcaption><a id="preview-v1"><img id="image-v1" alt="V1 corrected RAW neutral render"></a></figure>
<figure id="panel-v2"><figcaption>V2 · session refinement</figcaption><a id="preview-v2"><img id="image-v2" alt="V2 corrected RAW neutral render"></a></figure>
</div>
<div id="meta" aria-live="polite"><div id="refinement"></div><div id="status"></div><div id="scores"></div></div>
<div class="downloads"><div class="links"><a id="v2Dng">V2 editable DNG</a><a id="v2Jpeg">V2 full-resolution JPEG</a><a id="v2Report">V2 measurements</a></div><div class="links"><a id="v1Dng">V1 editable DNG</a><a id="v1Jpeg">V1 full-resolution JPEG</a><a id="v1Report">V1 measurements</a></div></div>
<div class="links">EXTRAS</div>
<footer>The row score is a diagnostic, not ground truth or a percentage of flicker removed. Real scene detail contributes to it. Correction uses deterministic gains and generates no image detail. Originals and V1 outputs are preserved.</footer>
</main><script>
const rows=DATA;
const $=id=>document.getElementById(id);
let index=0;
const readable=value=>value.replaceAll('_',' ');
rows.forEach((row,i)=>$('picker').add(new Option(row.name+' · '+row.shutter+' · '+row.refinement,i)));
function show(i){
  index=(i+rows.length)%rows.length;
  const row=rows[index];
  $('picker').value=String(index);
  $('count').textContent=(index+1)+' / '+rows.length;
  for(const key of ['original','v1','v2']){
    $('image-'+key).src=row.images[key];
    $('preview-'+key).href=row.images[key];
  }
  for(const [key,value] of Object.entries(row.downloads))$(key).href=value;
  $('refinement').textContent=row.name+' · '+row.refinement;
  $('status').textContent='V1: '+readable(row.v1Status)+' · V2: '+readable(row.v2Status)+' · '+readable(row.refinementReason);
  $('scores').textContent='Luminance row score · Original '+row.scores[0].toFixed(4)+' · V1 '+row.scores[1].toFixed(4)+' · V2 '+row.scores[2].toFixed(4);
}
function setView(view){
  $('panels').dataset.view=view;
  for(const key of ['original','v1','v2'])$('panel-'+key).hidden=!(view==='all'||view===key||(view==='compare'&&key!=='original'));
  document.querySelectorAll('button[data-view]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.view===view)));
}
$('picker').addEventListener('change',event=>show(Number(event.target.value)));
$('prev').addEventListener('click',()=>show(index-1));
$('next').addEventListener('click',()=>show(index+1));
document.querySelectorAll('button[data-view]').forEach(button=>button.addEventListener('click',()=>setView(button.dataset.view)));
document.addEventListener('keydown',event=>{
  if(/^(INPUT|SELECT|TEXTAREA)$/.test(event.target.tagName))return;
  if(event.key==='ArrowLeft'){event.preventDefault();show(index-1);}
  if(event.key==='ArrowRight'){event.preventDefault();show(index+1);}
});
show(Math.max(0,rows.findIndex(row=>row.refined)));
</script></body></html>"""
    page = (
        page.replace("COUNT", str(len(rows)))
        .replace("EXTRAS", "".join(extras))
        .replace("DATA", data)
    )
    destination = v2_root / "versions.html"
    destination.write_text(page)
    return destination
