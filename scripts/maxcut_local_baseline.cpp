// CPU quality baseline only: positive-gain sequential single-spin flips.
#include <algorithm>
#include <chrono>
#include <fstream>
#include <iostream>
#include <numeric>
#include <stdexcept>
#include <vector>
using I = long long;
int main(int argc, char** argv) {
  if (argc != 6) return 2;
  std::ifstream graph(argv[1]), initial(argv[2]), order_file(argv[3]);
  int n; I ignored; graph >> n >> ignored;
  std::vector<std::vector<std::pair<int,I>>> adj(n);
  int u,v; I w;
  while(graph >> u >> v >> w) {
    if(u==v) throw std::runtime_error("self edge unsupported");
    adj.at(u-1).push_back({v-1,w});
  }
  std::vector<int> s(n), order(n); std::vector<I> gain(n,0);
  for(int& x:s) { if(!(initial>>x) || (x!=-1 && x!=1)) return 3; }
  for(int& x:order) if(!(order_file>>x) || x<0 || x>=n) return 4;
  auto score=[&](){I total=0; for(int i=0;i<n;i++) for(auto [j,a]:adj[i]) if(s[i]!=s[j]) total+=a; return total/2;};
  for(int i=0;i<n;i++) for(auto [j,a]:adj[i]) gain[i]+=a*s[i]*s[j];
  I cut=score(); auto start=std::chrono::steady_clock::now();
  std::cout << "sweep,cut,flips,wall_seconds\n0," << cut << ",0,0\n";
  for(int sweep=1;sweep<=std::stoi(argv[4]);sweep++) {
    I flips=0;
    for(int i:order) if(gain[i]>0) {
      I delta=gain[i]; int old=s[i]; s[i]=-old; cut+=delta; gain[i]=-delta; ++flips;
      for(auto [j,a]:adj[i]) gain[j]-=2*a*s[j]*old;
      if(n<=32) { // Independent objective/gain audit on every smoke-test flip.
        if(cut!=score()) throw std::runtime_error("objective mismatch");
        for(int k=0;k<n;k++){I g=0;for(auto [j,a]:adj[k]) g+=a*s[k]*s[j];if(g!=gain[k])throw std::runtime_error("gain mismatch");}
      }
    }
    std::cout << sweep << ',' << cut << ',' << flips << ','
      << std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count() << std::endl;
    if(!flips) break;
  }
  if(cut!=score()) throw std::runtime_error("final objective mismatch");
  std::ofstream final(argv[5]); for(int x:s) final << x << '\n';
}
