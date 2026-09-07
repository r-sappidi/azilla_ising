module shared_fetch_replay_tb;
  logic clk=0, rst, icv, icr, iwv, iwr, ocv, ocr, owv, owr;
  logic [15:0] ia,ib,oa,ob;
  logic [255:0] idata,odata;
  integer c,j=0,row=0,blocks=0,beats=0,commands=0;
  shared_fetch_replay dut(clk,rst,icv,icr,ia,ib,iwv,iwr,idata,
                         ocv,ocr,oa,ob,owv,owr,odata);
  initial begin
    for(c=0;c<2200;c=c+1) begin
      clk=0; rst=(c==0 || c==700);
      icv=(j<9 && c%5!=1);ia=j+1;ib=j+11;
      iwv=(c%7!=2);idata={8{32'(j*100+row)}};
      ocr=(c%11>2);owr=(c%13>4);
      #5;
      $display("REPLAY c=%0d rst=%0d icv=%0d icr=%0d ia=%0d ib=%0d iwv=%0d iwr=%0d idata=%h ocv=%0d ocr=%0d oa=%0d ob=%0d owv=%0d owr=%0d odata=%h",c,rst,icv,icr,ia,ib,iwv,iwr,idata,ocv,ocr,oa,ob,owv,owr,odata);
      if(rst) begin j=0;row=0; end
      else begin
        if(icv && icr) begin j=j+1;row=0;blocks=blocks+1;end
        if(iwv && iwr) row=row+1;
        if(ocv && ocr) commands=commands+1;
        if(owv && owr) beats=beats+1;
      end
      clk=1;#5;
    end
    if(commands<12 || beats<384) $fatal(1,"insufficient replay coverage");
    $display("PASS SHARED_FETCH_REPLAY commands=%0d beats=%0d",commands,beats);
    $finish;
  end
endmodule
