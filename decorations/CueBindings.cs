namespace SourceGuides;

// Authored arctap identity resolved by the exporter after all chart adaptation.
public sealed class CueBinding
{
    public double sourceTime { get; set; }
    public double time { get; set; }
    public double x { get; set; }
    public int side { get; set; }
}

public static class CueBindings
{
    public static void Validate(Arc arc)
    {
        double sourceLast = double.NegativeInfinity, targetLast = sourceLast;
        foreach (var b in arc.bindings)
        {
            if (!double.IsFinite(b.sourceTime) || !double.IsFinite(b.time) || !double.IsFinite(b.x)
                || b.sourceTime < arc.start-1e-8 || b.sourceTime > arc.end+1e-8
                || b.sourceTime <= sourceLast || b.time <= targetLast || b.side < 1 || b.side > 4)
                throw new InvalidDataException("Invalid final-note guide binding");
            sourceLast = b.sourceTime; targetLast = b.time;
        }
    }

    // Piecewise affine clock adjustment ONLY for this explicit cue carrier.
    // Musical timing and independent decorative traces never change.
    public static double Time(Arc arc, double value, bool inverse = false)
    {
        var bindings = arc.bindings;
        if (bindings.Length == 0) return value;
        double From(CueBinding b) => inverse ? b.time : b.sourceTime;
        double To(CueBinding b) => inverse ? b.sourceTime : b.time;
        if (value <= From(bindings[0])) return value + To(bindings[0])-From(bindings[0]);
        for (int i=1; i<bindings.Length; i++)
        {
            var a=bindings[i-1]; var b=bindings[i];
            if (value <= From(b))
                return To(a)+(To(b)-To(a))*(value-From(a))/(From(b)-From(a));
        }
        return value + To(bindings[^1])-From(bindings[^1]);
    }

    public static (double x, double y) Point(Arc arc, double u)
    {
        var original=arc.RawPoint(u);
        if (arc.bindings.Length == 0) return original;
        double t=arc.start+(arc.end-arc.start)*u;
        (double x, double y) Delta(CueBinding b)
        {
            double v=arc.end>arc.start ? (b.sourceTime-arc.start)/(arc.end-arc.start) : 0;
            var p=arc.RawPoint(v);
            // Converted sky taps live on the native input plane in flight too.
            return (2*b.x-.5-p.x,-p.y);
        }
        var offset=Delta(arc.bindings[0]);
        for (int i=1; i<arc.bindings.Length && t>arc.bindings[i-1].sourceTime; i++)
        {
            var a=arc.bindings[i-1]; var b=arc.bindings[i];
            double v=Math.Clamp((t-a.sourceTime)/(b.sourceTime-a.sourceTime),0,1);
            v=v*v*(3-2*v);
            var first=Delta(a);var last=Delta(b);
            offset=(first.x+(last.x-first.x)*v,first.y+(last.y-first.y)*v);
        }
        return (original.x+offset.x,original.y+offset.y);
    }
}
