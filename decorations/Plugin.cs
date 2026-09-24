using BepInEx;
using BepInEx.Unity.IL2CPP;
using System.Security.Cryptography;
using UnityEngine;
using ifapp.Game;
using ifapp.Game.Scenes.Game;
using Object = UnityEngine.Object;

namespace SourceGuides;

[BepInPlugin("local.infalsus.sourceguides", "Source Guide Lines", "29.0.0")]
public sealed class Plugin : BasePlugin
{
    internal static Plugin Instance = null!;
    internal static Dictionary<string,Chart> Charts = new(StringComparer.Ordinal);
    const string GameHash = "ab1d8fa7739078510fab5f8580095c90ea0f5e9236ac9b918e934cb9ae1b7d9c";
    internal static string Hash(string path) => Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(path))).ToLowerInvariant();
    public override void Load()
    {
        Instance = this;
        if (Hash(Path.Combine(Paths.GameRootPath,"GameAssembly.dll")) != GameHash)
            throw new InvalidOperationException("Game build changed; source guides disabled.");
        var data = GuideSet.Read(Path.Combine(Path.GetDirectoryName(typeof(Plugin).Assembly.Location)!,"guides.json"));
        if (data.gameHash != GameHash) throw new InvalidDataException("Guide game-build identity differs.");
        Charts = data.charts.ToDictionary(c=>c.name, StringComparer.Ordinal);
        AddComponent<GuideRenderer>();
        Log.LogInfo($"Source guides ready for {Charts.Count} charts; visual geometry only.");
    }
}

public sealed class GuideRenderer : MonoBehaviour
{
    public GuideRenderer(IntPtr pointer) : base(pointer) { }
    readonly List<LineRenderer> lines = new();
    readonly List<Vector3> points = new(33);
    Material? material;
    GameObject? root;
    Chart? selected;
    string selectedName = "";
    IntPtr selectedPlayer;
    IntPtr selectedTrack;
    bool failed;

    static string ChartName(string? name)
    {
        if (string.IsNullOrEmpty(name)) return "";
        return Path.GetFileNameWithoutExtension(name.Replace('\\','/'))+".spc";
    }

    void Hide()
    {
        if (root) root!.SetActive(false);
    }

    void ClearSelection()
    {
        Hide(); selected=null; selectedName=""; selectedPlayer=IntPtr.Zero; selectedTrack=IntPtr.Zero;
    }

    void DestroyGeometry()
    {
        if (root) Object.Destroy(root);
        if (material) Object.Destroy(material);
        lines.Clear(); root=null; material=null;
    }

    public void OnDestroy() => DestroyGeometry();

    public void LateUpdate()
    {
        if (failed) return;
        try
        {
            var track=Track._mMA;
            var player=LogicalNotePlayer._Qe;
            if (!track || !player || !track.gameObject.activeInHierarchy || !player.gameObject.activeInHierarchy
                || !track.notesContainer) { ClearSelection(); return; }
            // _Ve is the logical chart name; _we may hold its alternate loader name.
            string name=ChartName(player._Ve);
            if (string.IsNullOrEmpty(name)) name=ChartName(player._we);
            if (name!=selectedName || player.Pointer!=selectedPlayer || track.Pointer!=selectedTrack)
            {
                DestroyGeometry(); selected=null; selectedName=name;
                selectedPlayer=player.Pointer; selectedTrack=track.Pointer;
                if (Plugin.Charts.TryGetValue(name,out var candidate))
                {
                    string encoded=Path.Combine(Paths.GameRootPath,"infalsus_Data","StreamingAssets","sam",candidate.guid);
                    if (File.Exists(encoded) && Plugin.Hash(encoded)==candidate.hash) selected=candidate;
                    else Plugin.Instance.Log.LogWarning($"Guide payload mismatch; disabled for {name}.");
                }
            }
            double now=track._bNA;
            if (selected==null || !double.IsFinite(now) || now < -10 || now>selected.duration+.5)
                { Hide(); return; }
            Draw(track,selected,now);
        }
        catch (Exception ex)
        {
            Hide(); failed=true;
            Plugin.Instance.Log.LogError($"Source guides stopped safely: {ex}");
        }
    }

    LineRenderer GetLine(int index, Track track)
    {
        if (!root)
        {
            root=new GameObject("Source guide geometry (no judgment)");
            root.transform.SetParent(track.notesContainer,false);
            var shader=Shader.Find("Sprites/Default") ?? Shader.Find("UI/Default");
            if (!shader) throw new InvalidOperationException("Guide shader unavailable");
            material=new Material(shader);
        }
        if (index==lines.Count)
        {
            var go=new GameObject("Guide arc");
            go.layer=track.notesContainer.gameObject.layer;
            go.transform.SetParent(root!.transform,false);
            var line=go.AddComponent<LineRenderer>();
            line.useWorldSpace=true;
            line.material=material;
            line.shadowCastingMode=UnityEngine.Rendering.ShadowCastingMode.Off;
            line.receiveShadows=false;
            line.numCapVertices=2;
            line.numCornerVertices=2;
            lines.Add(line);
        }
        return lines[index];
    }

    void Draw(Track track, Chart chart, double now)
    {
        var left=track._dBA(0);
        var right=track._dBA(1);
        float width=Vector3.Distance(left,right);
        double speed=track._RnA * Track._cMA;
        if (!float.IsFinite(width) || width<.01f || !double.IsFinite(speed) || speed<=0)
            { Hide(); return; }
        var forward=track.notesContainer.forward;
        var up=track.notesContainer.up;
        double far=width*GuideGeometry.VisibleDepthInWidths;
        int used=0;
        foreach (var motion in chart.Motions)
        {
            if (motion.HiddenAt(now)) continue;
            double distanceNow=motion.Distance(now);
            foreach (var window in motion.VisibleWindows(now,far/speed))
            for (int i=motion.FirstPossiblyVisible(window.low); i<motion.arcs.Length; i++)
            {
                var arc=motion.arcs[i];
                if (arc.RenderStart>window.high+1e-9) break;
                points.Clear();
                foreach (var p in arc.Samples(window.low,window.high))
                {
                    // Native world anchors follow camera/track transforms; no HUD plane.
                    float depth=(float)((motion.Distance(p.time)-distanceNow)*speed);
                    var anchor=Vector3.LerpUnclamped(left,right,
                        (float)GuideGeometry.NativeFieldParameter(p.x));
                    float height=(float)GuideGeometry.Height(p.y)*width;
                    // Lift the full stroke above the track mesh through judge contact.
                    points.Add(anchor+up*(height+width*(float)GuideGeometry.SurfaceLiftInWidths)+forward*depth);
                }
                if (points.Count<2) continue;
                var line=GetLine(used++,track);
                line.enabled=true;
                line.startWidth=line.endWidth=width*.0035f;
                var tint=arc.noinput ? new Color(.43f,.77f,.83f,.47f) : new Color(.66f,.73f,.77f,.56f);
                line.startColor=line.endColor=tint;
                line.positionCount=points.Count;
                for (int j=0;j<points.Count;j++) line.SetPosition(j,points[j]);
            }
        }
        // Arcaea's authored camera lead-in previews the two extra physical
        // lanes; the lane control then moves their rails to the side buttons.
        double lane=LaneScene.At(chart.laneScene.lanes,now);
        double camera=LaneScene.At(chart.laneScene.camera,now);
        double expansion=lane+.25*camera*(1-lane);
        if (expansion>1e-4)
        {
            for (int side=0;side<2;side++)
            for (int rail=0;rail<2;rail++)
            {
                // The outer rail flares toward each side button; the inner
                // separator reveals two extra physical lanes inside the field.
                float x=(float)(rail==0
                    ? (side==0 ? .125*(1-expansion) : 1-.125*(1-expansion))
                    : (side==0 ? .25-expansion/12 : .75+expansion/12));
                var anchor=Vector3.LerpUnclamped(left,right,x)
                    +up*(width*(float)GuideGeometry.SurfaceLiftInWidths);
                var line=GetLine(used++,track);
                line.enabled=true;
                line.startWidth=line.endWidth=width*(rail==0 ? .005f : .003f);
                var tint=new Color(.43f,.88f,1f,(float)(rail==0 ? .12+.42*expansion : .10+.28*expansion));
                line.startColor=line.endColor=tint;
                line.positionCount=2;
                line.SetPosition(0,anchor);
                line.SetPosition(1,anchor+forward*(float)far);
            }
        }
        for (int i=used;i<lines.Count;i++) lines[i].enabled=false;
        if (root) root!.SetActive(used>0);
    }
}
