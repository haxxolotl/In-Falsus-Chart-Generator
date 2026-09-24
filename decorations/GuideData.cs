using System.Text.Json;

namespace SourceGuides;

public static class GuideGeometry
{
    public const double VisibleDepthInWidths = 8;
    public const double SurfaceLiftInWidths = .002;
    // AFF's standard trace interval is [-.5, 1.5], not [0, 1]. The four
    // floor centers (-.25,.25,.75,1.25) become native (1,3,5,7)/8.
    // Keep intentional source excursions; only normalize the coordinate frame.
    public static double NativeFieldParameter(double sourceX) => .25 + .5 * sourceX;

    // Preserve authored sky height through the judge plane. Visibility is
    // clipped by arc time and travel distance, not by flattening its shape.
    public static double Height(double sourceY) => sourceY * .35;
}

public sealed class GuideSet
{
    public int version { get; set; }
    public string gameHash { get; set; } = "";
    public Chart[] charts { get; set; } = Array.Empty<Chart>();
    public static GuideSet Read(string path)
    {
        var set = JsonSerializer.Deserialize<GuideSet>(File.ReadAllText(path)) ?? throw new InvalidDataException("No guide data");
        if (set.version is not (1 or 2)) throw new InvalidDataException("Unknown guide data version");
        var names = new HashSet<string>(StringComparer.Ordinal);
        foreach (var chart in set.charts)
        {
            if (!names.Add(chart.name) || !chart.name.StartsWith("custom_", StringComparison.Ordinal)
                || !chart.name.EndsWith(".spc", StringComparison.Ordinal)
                || chart.guid.Length != 32 || !chart.guid.All(Uri.IsHexDigit)
                || chart.hash.Length != 64 || !chart.hash.All(Uri.IsHexDigit)
                || !double.IsFinite(chart.duration) || chart.duration <= 0)
                throw new InvalidDataException("Invalid chart identity");
            double last = double.NegativeInfinity;
            foreach (var control in chart.scroll)
            {
                if (control.Length != 2 || !double.IsFinite(control[0]) || !double.IsFinite(control[1])
                    || control[0] < last)
                    throw new InvalidDataException("Invalid installed scroll controls");
                last = control[0];
            }
            foreach (var arc in chart.arcs) CueBindings.Validate(arc);
            chart.laneScene.Validate();
            chart.arcs = chart.arcs.OrderBy(a=>a.RenderStart).ThenBy(a=>a.RenderEnd).ToArray();
            last = double.NegativeInfinity;
            chart.PrefixEnd = new double[chart.arcs.Length];
            double maxEnd = double.NegativeInfinity;
            for (int i = 0; i < chart.arcs.Length; i++)
            {
                var a = chart.arcs[i];
                if (!new[] {a.start,a.end,a.x0,a.x1,a.y0,a.y1}.All(double.IsFinite)
                    || a.RenderStart < last || a.end < a.start || !Arc.Kinds.Contains(a.easing)
                    || (!a.trace && !a.noinput)) throw new InvalidDataException("Invalid render-only arc");
                last = a.RenderStart;
                chart.PrefixEnd[i] = maxEnd = Math.Max(maxEnd, a.RenderEnd);
            }
            var groups = set.version == 2 ? chart.groups : Array.Empty<GuideGroup>();
            if (set.version == 2 && groups.Length == 0)
                throw new InvalidDataException("Version 2 guide motion is missing");
            if (groups.Length > 0)
            {
                if (groups.Select(g=>g.id).Distinct().Count() != groups.Length
                    || groups.Any(g=>g.id < 0 || g.id >= groups.Length)
                    || chart.arcs.Any(a=>a.group < 0 || a.group >= groups.Length))
                    throw new InvalidDataException("Invalid AFF guide group identity");
                chart.Motions = groups.Select(g=>new GroupMotion(g,chart.arcs.Where(a=>a.group==g.id).ToArray(),chart.duration,g.twoSided))
                    .Where(g=>g.arcs.Length>0).ToArray();
            }
            else chart.Motions = new[] {new GroupMotion(new GuideGroup {scroll=chart.scroll},chart.arcs,chart.duration,false)};
        }
        return set;
    }
}

public sealed class Chart
{
    public string name { get; set; } = "";
    public string title { get; set; } = "";
    public string guid { get; set; } = "";
    public string hash { get; set; } = "";
    public double duration { get; set; }
    public double[][] scroll { get; set; } = Array.Empty<double[]>();
    public GuideGroup[] groups { get; set; } = Array.Empty<GuideGroup>();
    public LaneScene laneScene { get; set; } = new();
    public Arc[] arcs { get; set; } = Array.Empty<Arc>();
    public double[] PrefixEnd = Array.Empty<double>();
    public GroupMotion[] Motions = Array.Empty<GroupMotion>();

    // The game's type-0 controls integrate seconds of travel, starting at speed 1.
    public double Distance(double seconds)
    {
        double prior = 0, speed = 1, total = 0;
        foreach (var control in scroll)
        {
            if (control[0] > seconds) break;
            total += (control[0]-prior)*speed;
            prior = control[0]; speed = control[1];
        }
        return total+(seconds-prior)*speed;
    }

    // Reversals and stops can make the visible time range disjoint. Intersect each
    // installed constant-speed segment with the camera's travel-distance window.
    public IEnumerable<(double low,double high)> VisibleWindows(double now, double travel)
    {
        double minimum=Distance(now), maximum=minimum+travel;
        double limit=Math.Max(duration,arcs.Length==0 ? duration : PrefixEnd[^1]);
        double low=now,speed=1;
        int i=0;
        while(i<scroll.Length && scroll[i][0]<=now) speed=scroll[i++][1];
        while(low<=limit)
        {
            double high=i<scroll.Length ? Math.Min(limit,scroll[i][0]) : limit;
            double d=Distance(low), from=low, to=high;
            if(speed==0)
            {
                if(d>=minimum-1e-10 && d<=maximum+1e-10) yield return (from,to);
            }
            else
            {
                double t0=low+(minimum-d)/speed,t1=low+(maximum-d)/speed;
                from=Math.Max(low,Math.Min(t0,t1)); to=Math.Min(high,Math.Max(t0,t1));
                if(from<=to) yield return (from,to);
            }
            if(high>=limit) break;
            low=high; speed=scroll[i++][1];
        }
    }

    public int FirstPossiblyVisible(double now)
    {
        int low=0, high=PrefixEnd.Length;
        while (low<high)
        {
            int mid = (low+high)/2;
            if (PrefixEnd[mid] < now-1e-9) low=mid+1; else high=mid;
        }
        return low;
    }
}

public sealed class LaneScene
{
    public double[][] lanes { get; set; } = Array.Empty<double[]>();
    public double[][] camera { get; set; } = Array.Empty<double[]>();

    public void Validate()
    {
        foreach (var events in new[] {lanes,camera})
        {
            double last=double.NegativeInfinity;
            foreach (var e in events)
            {
                if (e.Length!=3 || !e.All(double.IsFinite) || e[0]<last || e[1]<0
                    || e[2] is not (0 or 1))
                    throw new InvalidDataException("Invalid AFF lane scene control");
                last=e[0];
            }
        }
    }

    public static double At(double[][] events, double now)
    {
        double from=0,to=0,start=0,duration=0;
        foreach (var e in events)
        {
            if (e[0]>now) break;
            from=duration==0 ? to : from+(to-from)*Math.Clamp((e[0]-start)/duration,0,1);
            start=e[0]; duration=e[1]; to=e[2];
        }
        return duration==0 ? to : from+(to-from)*Math.Clamp((now-start)/duration,0,1);
    }
}

public sealed class GuideGroup
{
    public int id { get; set; }
    public bool twoSided { get; set; } = true;
    public double[][] scroll { get; set; } = Array.Empty<double[]>();
    public double[][] hidden { get; set; } = Array.Empty<double[]>();
}

// Source motion belongs only to overlay arcs. The native notes and their clock
// continue using the installed chart's independent safe scroll profile.
public sealed class GroupMotion
{
    public readonly int id;
    public readonly double[][] scroll;
    public readonly double[][] hidden;
    public readonly Arc[] arcs;
    public readonly bool twoSided;
    public readonly double duration;
    readonly double[] prefixEnd;

    public GroupMotion(GuideGroup group, Arc[] sourceArcs, double chartDuration, bool sourceMotion)
    {
        id=group.id; scroll=group.scroll; hidden=group.hidden; twoSided=sourceMotion;
        arcs=sourceArcs.OrderBy(a=>a.RenderStart).ThenBy(a=>a.RenderEnd).ToArray();
        duration=chartDuration;
        prefixEnd=new double[arcs.Length];
        double end=double.NegativeInfinity;
        for(int i=0;i<arcs.Length;i++) prefixEnd[i]=end=Math.Max(end,arcs[i].RenderEnd);
        double last=double.NegativeInfinity;
        foreach(var control in scroll)
        {
            if(control.Length!=2 || !double.IsFinite(control[0]) || !double.IsFinite(control[1])
                || control[0]<last || Math.Abs(control[1])>1_000_000)
                throw new InvalidDataException("Invalid AFF guide speed");
            last=control[0];
        }
        last=double.NegativeInfinity;
        foreach(var control in hidden)
        {
            if(control.Length!=2 || !double.IsFinite(control[0]) || control[0]<last
                || control[1] is not (0 or 1))
                throw new InvalidDataException("Invalid AFF hidegroup control");
            last=control[0];
        }
    }

    public bool HiddenAt(double now)
    {
        bool result=false;
        foreach(var control in hidden)
        {
            if(control[0]>now) break;
            result=control[1]==1;
        }
        return result;
    }

    public double Distance(double seconds)
    {
        double prior=0,speed=1,total=0;
        foreach(var control in scroll)
        {
            if(control[0]>seconds) break;
            total+=(control[0]-prior)*speed;
            prior=control[0]; speed=control[1];
        }
        return total+(seconds-prior)*speed;
    }

    public IEnumerable<(double low,double high)> VisibleWindows(double now, double travel)
    {
        double center=Distance(now), minimum=center-(twoSided ? travel : 0), maximum=center+travel;
        double limit=Math.Max(duration,arcs.Length==0 ? duration : prefixEnd[^1]);
        double low=twoSided ? 0 : now,speed=1;
        int i=0;
        while(i<scroll.Length && scroll[i][0]<=low) speed=scroll[i++][1];
        while(low<=limit)
        {
            double high=i<scroll.Length ? Math.Min(limit,scroll[i][0]) : limit;
            double d=Distance(low), from=low, to=high;
            if(speed==0)
            {
                if(d>=minimum-1e-10 && d<=maximum+1e-10) yield return (from,to);
            }
            else
            {
                double t0=low+(minimum-d)/speed,t1=low+(maximum-d)/speed;
                from=Math.Max(low,Math.Min(t0,t1)); to=Math.Min(high,Math.Max(t0,t1));
                if(from<=to) yield return (from,to);
            }
            if(high>=limit) break;
            low=high; speed=scroll[i++][1];
        }
    }

    public int FirstPossiblyVisible(double now)
    {
        int low=0,high=prefixEnd.Length;
        while(low<high)
        {
            int mid=(low+high)/2;
            if(prefixEnd[mid]<now-1e-9) low=mid+1; else high=mid;
        }
        return low;
    }
}

public sealed class Arc
{
    public static readonly HashSet<string> Kinds = new() {"s","b","si","so","sisi","siso","sosi","soso"};
    public double start { get; set; }
    public double end { get; set; }
    public double x0 { get; set; }
    public double x1 { get; set; }
    public double y0 { get; set; }
    public double y1 { get; set; }
    public string easing { get; set; } = "s";
    public bool trace { get; set; }
    public bool noinput { get; set; }
    public int group { get; set; }
    public CueBinding[] bindings { get; set; } = Array.Empty<CueBinding>();
    public double RenderStart => CueBindings.Time(this,start);
    public double RenderEnd => CueBindings.Time(this,end);

    static double Ease(double u, string kind) => kind switch
    {
        "s" => u, "si" => Math.Sin(Math.PI*u*.5), "so" => 1-Math.Cos(Math.PI*u*.5),
        "b" => u*u*(3-2*u), _ => throw new InvalidDataException("Unknown easing")
    };

    public (double x, double y) Point(double u) => CueBindings.Point(this,Math.Clamp(u,0,1));

    public (double x, double y) RawPoint(double u)
    {
        u = Math.Clamp(u, 0, 1);
        string xKind = easing.Length == 4 ? easing[..2] : easing;
        string yKind = easing.Length == 4 ? easing[2..] : easing == "b" ? "b" : "s";
        return (x0+(x1-x0)*Ease(u,xKind), y0+(y1-y0)*Ease(u,yKind));
    }

    // Shared by the plugin and the moving proof exporter, including clipping.
    public IEnumerable<(double time, double x, double y)> Samples(double now, double horizon)
    {
        if (RenderEnd < now-1e-9 || RenderStart > horizon+1e-9) yield break;
        double span = end-start;
        double first = span > 0 ? Math.Clamp((CueBindings.Time(this,now,true)-start)/span,0,1) : 0;
        double last = span > 0 ? Math.Clamp((CueBindings.Time(this,horizon,true)-start)/span,0,1) : 1;
        int segments = Math.Max(2,(int)Math.Ceiling(32*(last-first)));
        var samples=new SortedSet<double>();
        for(int i=0;i<=segments;i++) samples.Add(first+(last-first)*i/segments);
        if(span>0) foreach(var b in bindings)
        {
            double u=(b.sourceTime-start)/span;
            if(u>=first && u<=last) samples.Add(u);
        }
        foreach(double u in samples)
        {
            var p=Point(u);
            yield return (CueBindings.Time(this,start+span*u),p.x,p.y);
        }
    }
}
