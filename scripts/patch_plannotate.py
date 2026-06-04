#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    if new in text:
        return
    if old not in text:
        raise SystemExit(f"patch target not found in {path}")
    path.write_text(text.replace(old, new))


def main() -> int:
    env = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("envs/plasmid-pipeline")
    pkg = env / "lib" / "python3.11" / "site-packages" / "plannotate"
    annotate = pkg / "annotate.py"
    infernal = pkg / "infernal.py"
    resources = pkg / "resources.py"
    bokeh_plot = pkg / "bokeh_plot.py"

    replace_once(
        annotate,
        "log = NamedTemporaryFile()\n",
        '''log = NamedTemporaryFile()

def numeric_compat(df, downcast=None):
    text_columns = {"sseqid", "qseq", "Feature", "Description", "accession", "clan name", "Type"}
    for col in df.columns:
        if col in text_columns:
            continue
        converted = pd.to_numeric(df[col], errors="coerce", downcast=downcast)
        if converted.notna().any() or df[col].isna().all():
            df[col] = converted
    return df
''',
    )
    replace_once(annotate, "inDf = inDf.apply(pd.to_numeric, errors='ignore')", "inDf = numeric_compat(inDf)")
    replace_once(
        annotate,
        'inDf = inDf.apply(pd.to_numeric, errors=\'ignore\', downcast = "integer")',
        'inDf = numeric_compat(inDf, downcast="integer")',
    )
    replace_once(
        annotate,
        "rowSlice = (seqSpace[columnSlice] == kind).any(1)",
        "rowSlice = (seqSpace[columnSlice] == kind).any(axis=1)",
    )

    replace_once(
        infernal,
        "import streamlit as st\n",
        '''import streamlit as st

def numeric_compat(df, downcast=None):
    text_columns = {"sseqid", "qseq", "Feature", "Description", "accession", "clan name", "Type"}
    for col in df.columns:
        if col in text_columns:
            continue
        converted = pd.to_numeric(df[col], errors="coerce", downcast=downcast)
        if converted.notna().any() or df[col].isna().all():
            df[col] = converted
    return df
''',
    )
    replace_once(
        infernal,
        "    with open(file_loc) as file_handle:\n        lines = file_handle.readlines()\n",
        '''    with open(file_loc) as file_handle:
        lines = file_handle.readlines()

    empty_columns = [
        "sseqid", "qstart", "qend", "sstart", "send", "sframe", "score",
        "evalue", "Feature", "Description", "qseq", "length", "slen",
        "pident",
    ]
    data_lines = [line for line in lines if line.strip() and not line.startswith("#")]
    if len(lines) < 2 or not data_lines:
        return pd.DataFrame(columns=empty_columns)
''',
    )
    replace_once(
        infernal,
        'infernal = infernal.apply(pd.to_numeric, errors=\'ignore\', downcast = "integer")',
        'infernal = numeric_compat(infernal, downcast="integer")',
    )

    replace_once(
        resources,
        '    inDf[\'Type\'] = inDf[\'Type\'].str.replace("origin of replication", "rep_origin")',
        '''    inDf['Type'] = inDf['Type'].fillna("misc_feature").astype(str)
    inDf.loc[inDf['Type'].isin(["", "nan", "None"]), 'Type'] = "misc_feature"
    inDf['Type'] = inDf['Type'].str.replace("origin of replication", "rep_origin")''',
    )

    replace_once(
        bokeh_plot,
        "from bokeh.models import HoverTool, ColumnDataSource, WheelZoomTool, Range1d, Legend, LegendItem\n",
        "from bokeh.models import HoverTool, ColumnDataSource, WheelZoomTool, Range1d, Legend, LegendItem\nfrom bokeh.transform import factor_cmap\n",
    )
    replace_once(
        bokeh_plot,
        "        calculated_levels = calculated_levels.append({'index' : index, 's' : s, 'e' : e, 'level' : new_level},\n        ignore_index = True)\n",
        "        calculated_levels.loc[len(calculated_levels)] = {'index' : index, 's' : s, 'e' : e, 'level' : new_level}\n",
    )
    replace_once(
        bokeh_plot,
        '    hover = HoverTool(names=["features"])\n',
        '    hover = HoverTool()\n',
    )
    replace_once(
        bokeh_plot,
        "    p = figure(plot_height=plotDimen,plot_width=plotDimen, title=\"\",\n",
        "    p = figure(height=plotDimen, width=plotDimen, title=\"\",\n",
    )
    replace_once(
        bokeh_plot,
        "    df=full.append(frag).reset_index(drop=True)#.set_index('Feature')\n",
        "    df=pd.concat([full, frag], ignore_index=True).reset_index(drop=True)#.set_index('Feature')\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
