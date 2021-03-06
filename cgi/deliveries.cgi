#!/usr/bin/perl -w

$| = 1;

################################################################################
#                                                                              #
# Progenetix site scripts                                                      #
#                                                                              #
# molecular cytogenetics, Comparative Genomic Hybridization, genomic arrays    #
# data analysis & visualization                                                #
#                                                                              #
# © 2000-2021 Michael Baudis: m@baud.is                                        #
#                                                                              #
################################################################################

=podmd

### Examples

* <https://progenetix.org/cgi/PGX/cgi/samplePlots.cgi?accessid=0fab5ffb-6d1b-45e6-8149-7a5ae9a67286&group_by=PMID>
  - obv. real accessid neeeded ...

=cut

BEGIN { unshift @INC, ('..') };

use strict;
use CGI::Simple;
use CGI::Carp qw(fatalsToBrowser);

use File::Basename;
use MongoDB;
use JSON::XS;

use Data::Dumper;

# local packages
use PGX;
use lib::CGItools;

$MongoDB::Cursor::timeout = 120000;
my $config = PGX::read_config();
my $params = lib::CGItools::deparse_query_string();

if ($params->{debug}->[0] > 0) {
	print 'Content-type: text/plain'."\n\n" }

my $accessid = $params->{accessid}->[0];

my $api = {
	config => $config,
	datasetid => '',
	coll => '',
	path_var => '/_process_'."$^T"."$$",
	plotargs =>	{ map{ $_ => join(',', @{ $params->{$_} }) } (grep{ /^\-\w+?$/ } keys %{ $params }) },
	handover_db => $config->{handover_db},
	handover_coll => $config->{handover_coll},
	accessid => $accessid,
	segfile => $config->{paths}->{dir_tmp_base_path}.'/'.$accessid,
	technology_keys => $config->{technology_keys},
	server_link => ($ENV{HTTPS} ? 'https://' : 'http://').$ENV{HTTP_HOST},
	data => { },
	output => 'json',
	errors => [ ],
	warnings => [ ]
};
bless $api;

$api->{plotargs}->{-path_loc} = $api->{config}->{paths}->{dir_tmp_base_path}.$api->{path_var};
$api->{server_link} =~ s/^(https?\:\/\/)\w+?\.(\w+?\.\w+?(\/.*?)?)$/$1$2/;
$api->{plotargs}->{-path_web} = $api->{server_link}.$api->{config}->{paths}->{web_tmp_base_path}.$api->{path_var};

if ($accessid =~ /[^\w\-]/) {
	push(
  		@{ $api->{errors} },
		"Wrong or missing accessid parameter $api->{accessid}.",
		"Wrong or missing segments file."
	);
}

mkdir $api->{plotargs}->{-path_loc};

################################################################################

$api->_retrieve_samples();

if ($params->{"do"}->[0] eq "cnvhistogram") {

	$api->{plotargs}->{ '-svg_embed' } = 1;
	$api->_return_histogram();
	print 'Content-type: image/svg+xml'."\n\n";
	print $api->{data}->{plots}->{ histogram }->{svg};
	exit;
	
}

$api->_return_histogram();
$api->_add_samplecollections();
$api->_return_multihistogram();
$api->_return_samplematrix();

$api->_return_json();

################################################################################

sub _retrieve_samples {

	my $api = shift;

	my $pgx = new PGX($api->{plotargs});
	$pgx->pgx_add_segments_from_file($api->{segfile});
	$pgx->pgx_create_samples_from_segments();
	if (! -f $api->{segfile}) {
		$pgx->pgx_open_handover($api->{config}, $api->{accessid});
		$pgx->pgx_samples_from_handover();
	}
	if ($api->{config}->{param}->{'-randno'}->[0] > 0) {
		$pgx->{samples} = BeaconPlus::ConfigLoader::RandArr($pgx->{samples}, $api->{config}->{param}->{'-randno'}->[0]) }
	$pgx->pgx_callset_labels_from_biosamples($api->{config});
	$pgx->pgx_add_variants_from_db();
	
	$api->{samples}	= $pgx->{samples};
	$api->{datasetid} = $pgx->{datasetid};
	
	return $api;

}

################################################################################

sub _return_histogram {

	my $api = shift;

	my $plotType = 'histogram';
	
	# modifying a copy of the standard plot arguments for the overview plot
	my $plotargs = bless { %{ $api->{plotargs} } }, ref $api->{plotargs};
	$plotargs->{'-plottype'} = $plotType;
	$plotargs->{-size_title_left_px} = 0;
	
	if (-f $api->{segfile}) {
		$plotargs->{-text_bottom_left} = 'Uploaded: '.scalar(@{ $api->{samples} }).' samples' }
	else {	
		$plotargs->{-text_bottom_left} = $api->{datasetid}.': '.scalar(@{ $api->{samples} }).' samples' }	

	my $plot = new PGX($plotargs);
	$plot->{datasetid} = $api->{datasetid};
	$plot->{parameters}->{plotid} = 'histogram';
	$plot->pgx_add_frequencymaps( [ { statusmapsets => $api->{samples} } ] );
	$plot->return_histoplot_svg();
	$plot->write_svg();
	
	if ($plot->{parameters}->{svg_embed} > 0) {
		$api->{data}->{plots}->{ $plotType }->{svg} = $plot->{svg} }
	$api->{data}->{plots}->{ $plotType }->{svg_link_tmp} = $plot->{svg_path_web};
	
	return $api;
	
}

################################################################################

sub _add_samplecollections {

	my $api = shift;

	my $pgx = new PGX($api->{plotargs});
	$pgx->{samples} = $api->{samples};

	$pgx->pgx_create_sample_collections();	
	$api->{samplecollections} = $pgx->{samplecollections};

	return $api;
	
}

################################################################################
# (clustered) CNA histograms
################################################################################

sub _return_multihistogram {

	my $api = shift;
	
	if (@{ $api->{samplecollections} } < 2) {
		return $api }
		
	my $plotType = 'multihistogram';
	
	my $plotargs = $api->{plotargs};
	$plotargs->{'-plottype'} = $plotType;	

	if (-f $api->{segfile}) {
		$plotargs->{-text_bottom_left} = 'Uploaded: '.scalar(@{ $api->{samples} }).' samples' }
	else {	
		$plotargs->{-text_bottom_left} = $api->{datasetid}.': '.scalar(@{ $api->{samples} }).' samples' }	
	
	my $maxName = PGX::lib::Helpers::MaxTextWidthPix(
					[ map{ $_->{name} } @{ $api->{samplecollections} } ],
					$plotargs->{-parameters}->{size_text_title_left_px}
				);
								
	if ($plotargs->{-size_title_left_px} < 10) {
		$plotargs->{-size_title_left_px} = $maxName }
	
	my $plot = new PGX($plotargs);
	$plot->{samples} = $api->{samples};

	$plot->{datasetid} = $api->{datasetid};
	$plot->{parameters}->{plotid} = $plotType;
	$plot->pgx_add_frequencymaps($api->{samplecollections});
	$plot->cluster_frequencymaps();
	$plot->return_histoplot_svg();
	$plot->write_frequency_matrix($api->{plotargs}->{-path_loc}.'/frequencymatrix.tsv');
	$plot->write_svg();

	if ($plot->{parameters}->{svg_embed} > 0) {
		$api->{data}->{plots}->{ $plotType }->{svg} = $plot->{svg} }

	$api->{data}->{plots}->{ $plotType }->{svg_link_tmp} = $plot->{svg_path_web};
	$api->{data}->{data_files}->{frequencymatrix}->{"link"} = $api->{plotargs}->{-path_web}.'/frequencymatrix.tsv';

	return $api;
	
}

# ################################################################################
# sample matrix ##################################################################
# ################################################################################

sub _return_samplematrix {

	my $api = shift;

	my $plotType = 'multistrip';
	my $plotargs = $api->{plotargs};
	$plotargs->{'-plottype'} = $plotType;
	
	if (-f $api->{segfile}) {
		$plotargs->{-text_bottom_left} = 'Uploaded: '.scalar(@{ $api->{samples} }).' samples' }
	else {	
		$plotargs->{-text_bottom_left} = $api->{datasetid}.': '.scalar(@{ $api->{samples} }).' samples' }	
	
	my $plot = new PGX($plotargs);
	$plot->{samples} = $api->{samples};
	$plot->{datasetid} = $api->{datasetid};
	$plot->{parameters}->{plotid} = $plotType;
	$plot->cluster_samples();
	$plot->return_stripplot_svg();
	$plot->write_svg();

	if ($plot->{parameters}->{svg_embed} > 0) {
		$api->{data}->{plots}->{ $plotType }->{svg} = $plot->{svg} }
	$api->{data}->{plots}->{ $plotType }->{svg_link_tmp} = $plot->{svg_path_web};
	
	$plot->write_status_matrix();
	$api->{data}->{samplematrix_link_tmp} = $plot->{samplematrix_link_tmp};
	
	return $api;

}

################################################################################
################################################################################
################################################################################

sub _return_json {
	
	my $api = shift;
  
	if ($api->{output} =~ /json/i) {
	  print	'Content-type: application/json'."\n\n";
	  print JSON::XS->new->pretty( 1 )->allow_blessed()->convert_blessed()->encode( { data => $api->{data}, errrors => $api->{errors}, warnings => $api->{warnings} } )."\n";
	  exit;
	}
	
	return $api;

}


1;
