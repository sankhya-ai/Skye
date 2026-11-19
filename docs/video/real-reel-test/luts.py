import math
def sm(x): return x*x*(3-2*x)
def mk(name,contrast,teal,warm,sat,lift):
    N=33
    with open(name,'w') as f:
        f.write(f'TITLE "{name}"\nLUT_3D_SIZE {N}\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n')
        for b in range(N):
            for g in range(N):
                for r in range(N):
                    R,G,B=r/(N-1),g/(N-1),b/(N-1)
                    # contrast around mid via smoothstep blend
                    def c(x): return x+(sm(x)-x)*contrast
                    R,G,B=c(R),c(G),c(B)
                    L=0.2126*R+0.7152*G+0.0722*B
                    # saturation
                    R=L+(R-L)*sat;G=L+(G-L)*sat;B=L+(B-L)*sat
                    sh=(1-L)**2; hi=L**2
                    R+=warm*hi*0.9 - teal*sh*0.6
                    G+=warm*hi*0.25 + teal*sh*0.15
                    B+=-warm*hi*0.7 + teal*sh*0.9
                    R=lift+(1-lift)*R;G=lift+(1-lift)*G;B=lift+(1-lift)*B
                    f.write('%.5f %.5f %.5f\n'%tuple(min(1,max(0,v)) for v in (R,G,B)))
mk('gta.cube',contrast=.9,teal=.10,warm=.10,sat=.95,lift=.015)
mk('paper.cube',contrast=.45,teal=.02,warm=.05,sat=1.08,lift=.0)
